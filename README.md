# Grounded Analytics Agent

*Ask the spreadsheet.* A data-analysis engine for messy real-world workbooks that keeps two promises
most LLM-over-data tools quietly break:

> **Every number links back to a specific cell. When the file can't answer, it says so instead of
> guessing.**

```
Q: What was Gujarat's diesel consumption in 2019-20?
   Gujarat consumed 5,607.58 thousand tonnes of diesel in 2019-20.
   from PT_Cons_Statewise HSD!M42   (the cell holds 5607.584527746732)

Q: How much tax revenue did Maharashtra generate from fuel in 2022-23?
   I can't answer this from the file. There is no tax column.
   It has: product, region, state, value, year

Q: How many states grew diesel by more than 20% between 2019-20 and 2024-25?
   15 states.
   from HSD!B10:B19, B22:B29, ...   (too complex for the core grammar, so it was written
                                     as SQL, checked, and still traced back to 631 cells)
```

The first is a lookup. The second is a refusal. The third is a harder question the core grammar
can't express, answered by a second path that still keeps every figure traceable. This document
explains how those three live together behind one contract.

(The examples all use one fuel workbook so they're easy to follow. The engine reads any `.xlsx` or
`.csv`, whether it's a messy government cross-tab or a tidy table of records, and carries nothing
over from one file to the next. See sections 4.2 and 4.3, and the example files in section 11.)

---

## Contents

1. [The core idea: the model never writes a number](#1-the-core-idea)
2. [Quickstart](#2-quickstart)
3. [Architecture](#3-architecture)
4. [Key decisions and tradeoffs](#4-key-decisions-and-tradeoffs) (with the papers behind them)
5. [How lineage works](#5-how-lineage-works)
6. [How refusal works](#6-how-refusal-works)
7. [Evaluations](#7-evaluations)
8. [Limitations](#8-limitations)
9. [Future improvements](#9-future-improvements)
10. [How the system grew](#10-how-the-system-grew)
11. [Appendix: Coreworks impressions, the files, and the worst bug](#11-appendix)

---

## 1. The core idea

The task asks for two things: numbers you can trace, and clean refusals. Put those together and
they rule out the obvious build, because together they mean one thing:

> The model is never allowed to produce a number. Not even a correct one.

So the model doesn't write figures. It writes a sentence with blanks:

```
"Gujarat consumed {v1} thousand tonnes of diesel in 2019-20."
```

and the database fills the blanks. A made-up number isn't just unlikely here. There is no path for
a model-written digit to reach the user at all. Three jobs, kept apart:

| job | who does it | the rule |
|---|---|---|
| read the question, decide what to compute | the model | never writes a digit |
| do the arithmetic | DuckDB | deterministic, repeatable |
| remember where each value came from | `cell_map` | recorded when the file is opened, never rebuilt later |

Every other choice in the system exists to protect that split.

---

## 2. Quickstart

```bash
python -m venv .venv && ./.venv/bin/pip install -r requirements.txt

# read a workbook into a DuckDB table plus the cell map (works for xlsx and csv)
./.venv/bin/python src/ingest_spec.py specs/ppac.yaml

# the API (serves answers as JSON) and the React UI
./.venv/bin/python -m uvicorn api:app --app-dir src --port 8000 &
npm --prefix frontend install && npm --prefix frontend run dev      # http://localhost:5173
```

It runs with no API key by falling back to a local `llama3.2` through Ollama. A stronger model
(DeepSeek, or any OpenAI-compatible one) turns on the harder question path. Add a key in the UI, or
copy `.env.example` to `.env`.

**Tests.** One small paid suite that goes through the real front door, and several free ones:

```bash
./.venv/bin/python tests/eval_gates.py        # 69 gate + 8 memory cases, instant, free
./.venv/bin/python tests/eval_corpus.py       # 8 ingest shapes, round-trip, free
./.venv/bin/python tests/eval_record_qa.py    # Q&A over a record table, free
./.venv/bin/python tests/eval_e2e.py          # 15 cases through ask(), about 2 min, ~$0.0015
```

---

## 3. Architecture

### The shape of it

```
                              the seam: one VALIDATED plan
                              |                                          |
 xlsx/csv -> ingest -> catalog ->  [ typed plan ]  -> compile -> execute |
             |         (structure   model fills a    checked    DuckDB + |
             v          only)        small form       SQL        cell_map |-> Answer -> renderers
          cell_map                                                        |   (numbers   React, PPT,
         (per-cell                [ SQL escape hatch ] -> sql_gate -> ----|    + cells)   deck agent
          receipts)                model writes SQL     AST check +  operator
                                   (hard questions)     read-only    DAG lineage
             ^                                            ^
         verify.py  <- refusal checks run at the earliest |
                       point their evidence still exists  |
```

Two answer paths meet at one place: the seam. Whether a plan comes from a 3B model filling in a JSON
form or a strong model writing SQL, everything after that point sees a checked plan and nothing else.
The exit is the `Answer` object. It's structured data (a sentence template, typed value slots, and
citations), not a chat string, so PPTX, PDF, or a second UI are renderers rather than rewrites.

### The modules

| module | what it does |
|---|---|
| `probe.py` | reads a sheet's shape (per row and column). Interprets nothing. |
| `propose.py` | heuristics guess the geometry, a model guesses the meaning, both go to a human to confirm |
| `spec.py`, `ingest_spec.py` | the frozen reading of a workbook, and the deterministic ingest it drives |
| `catalog.py` | the model's whole view of the data. Structure only, no values. |
| `plan.py` | the shapes a plan can take: `query`, `derived`, `abstain` |
| `planner.py` | the provider registry and a cheap-first cascade (try local, escalate on failure) |
| `compile.py` | turns a plan into checked SQL plus a second query for provenance |
| `execute.py` | runs both, attaches citations, builds the `Answer` |
| `verify.py` | the refusal checks (plain functions, no model, no network) |
| `sql_gate.py`, `sql_dag.py`, `sql_path.py` | the SQL path: safety check, operator-DAG lineage, and the glue |
| `memory.py` | 2-turn memory that tracks where each carried-over filter came from |
| `ask.py` | the front door. Runs the checks, the plan, the escape hatch, and memory. |
| `render/` | `deck.py` (designed PPTX), `deck_agent.py` (agent-authored decks), and a React client in `frontend/` |

### The rules the design protects

These are the sentences the whole thing exists to keep true.

- Lineage is about *how the file was read*, not about the data. You record it cell by cell at ingest,
  and you can't recover it afterward (section 5). One `pd.read_excel()` and it's gone.
- The seam is the checked plan, not the model's output format. Swap the model, or even swap the typed
  plan for raw SQL, and nothing downstream changes.
- Fix a trap where it's impossible to get wrong, not by asking the model politely. The compiler
  injects the safe defaults. Anything you ask a model to remember, it eventually forgets.
- Put a check at the earliest point where the information it needs still exists. Four separate bug
  classes were really one mistake: a check that ran after its evidence was gone.
- No number without lineage. The SQL path ships an answer only if the cells trace. If they don't, it
  refuses. Traceability isn't bolted on at the end; it's the gate.

---

## 4. Key decisions and tradeoffs

Each decision below settles a real fork. The full list is numbered `D1..D86` in
[docs/DECISIONS.md](docs/DECISIONS.md); this is the important subset, with the prior work behind each.

### 4.1 A checked plan, not raw SQL from the model

| rejected | why |
|---|---|
| RAG over spreadsheet chunks | the model does the math (you can't verify it), and lineage becomes "here are the chunks I read" |
| model writes pandas, we `exec` it | you can't statically check arbitrary Python, and you can't recover which rows produced the answer |
| let the model produce numbers, then validate them | that's a filter, not a guarantee. Under a plan a bad number can't be produced at all. |
| refusal as an `except` branch | it fires when code breaks and stays silent when data is missing, which is backwards |

**The decision (D5, D22).** A typed JSON plan (`query`, `derived`, `abstain`). You can check it before
running it, and row-level provenance falls right out of it. The seam is this checked plan, not the
model's output format, so a stronger model can slot in behind the same validator without touching
anything downstream. The cost is a ceiling on what the plan can express, and section 4.4 fixes that
with a second path instead of loosening the check.

### 4.2 Ingest: geometry is mechanical, meaning needs a model, structure gets confirmed

Reading a workbook nobody has seen splits neatly by what happens when you get it wrong.

| | who decides | if it's wrong |
|---|---|---|
| geometry (header row, label column, value block, total rows) | heuristics, no model | every number and every citation is wrong, and confidently so |
| meaning (what a row is, what a period is called, aliases, units) | a model, shown structure only | the assistant just understands questions a bit worse |
| confirmation | a human, once | nothing to lose |

Only the first is dangerous, so only the first is model-free. This lines up with what the research
found: no automatic structure detector is accurate enough to run unchecked.

- **TableSense** (Dong et al., AAAI 2019): a trained CNN for finding table boundaries gets 86.5%
  precision and 91.3% recall at plus-or-minus 2 cells. It's still wrong about 1 table in 8.
- **Pytheas** (Christodoulakis et al., VLDB 2020): learned rules label CSV lines as header, data, or
  footnote. It gets 95.6% of files fully right, using the "the header's pattern differs from its
  column's body" test that this project reuses.
- **SpreadsheetLLM / SheetCompressor** (Microsoft, 2024): even a fine-tuned frontier model tops out
  near 79% on table detection. Its trick of keeping the odd-looking rows and dropping the repetitive
  runs is the same thing `probe.sketch()` does. I built that before reading the paper.
- **Flatfile and Trifacta** (products): they auto-map, then always show a human a review screen. The
  review screen is the product, not a fallback.

**The decision (D16, D58, D59).** Split ingest into two halves. A universal reader (`probe.py`,
`propose.py`) figures out the geometry with heuristics and no model, then a model names the meaning
from structure alone. A human confirms both once on a review screen, and only what a person can't see
gets asked (the fact that a sheet named `PT_Cons_Statewise MS` means petrol lives in the sheet name,
not in any cell). After that the reading is frozen into `specs/<file>.yaml`, and ingest is fully
deterministic. The confirmation screen is required, not a fallback, because even the best published
detector is wrong 1 in 8, and a wrong header row is just a hallucination moved into the ingest layer
where nobody thinks to look. The rejected alternative was auto-detecting everything, which is the more
dangerous option, not the safer one: silently guessing the header makes every number and every
citation wrong, and confidently so.

### 4.3 Cross-tab vs record table: the tidy-data test

A government cross-tab (state by year, one measure) and a tidy record table (one stock per row, many
metric columns) are opposite shapes. Force one layout onto both and you silently wreck the other. The
decision is a mechanical version of one question from **Wickham, "Tidy Data"** (JSS 59(10), 2014):

> Would the column headers make sense as *values* of one variable, or as *names* of different
> variables?

`2008-09, 2009-10, ...` are values of "year", so the header is folded-up data and you unpivot it.
`Name, ISIN, Stage, % vs pivot` are names of different things, so the table is already tidy and you
leave it alone. This is exactly the taxonomy in **Auto-Tables** (Li, He et al., VLDB 2023, best
paper), whose learned operators include a `none` class "so that we do not over-trigger on tables that
require no transformation", which is the same bug (melting a tidy table) my classifier had to stop
doing.

**The decision (D67).** Classify each sheet as cross-tab or record before ingest, and act on the
verdict: a cross-tab gets unpivoted (its header row turns into a `period` column), a record table is
kept as it is (its columns stay put). Two independent tests vote. One asks whether the value-column
headers look periodic (years, quarters). The other asks which way sameness runs, down the columns or
across the rows. When they agree it's high confidence. When they disagree, or the headers are merged
and bilingual like the Union Budget's, it drops to low confidence and gets flagged for a human. A
wrong flip here is the same class of danger as a wrong header row, so it's confirmed, never silently
guessed. A single non-periodic value column (a two-column list like City and Population) is still a
record, at medium confidence.

### 4.4 Hard questions: a bigger typed grammar, or letting the model write SQL

The typed plan can express one aggregate, or one two-measure calculation. It can't do "average growth
across all 36 states", which needs a number per state and then an average of those. There are two
ways to lift that ceiling. I built both and measured them on that exact question (see `prototypes/`).

| | A: a richer typed grammar | B: the model writes SQL |
|---|---|---|
| both returned | 71.65% | 71.65% (the same, so this isn't about being correct) |
| lineage | a precise tree, for free (the plan is the operator tree) | recovered from the SQL. Exact for tidy shapes, weaker on windows or `DISTINCT`. |
| what it can express | only the grammar's shapes (one operator added is one shape) | anything SQL can say |
| how it fails | "I can't express that", loudly | "here's the number, no lineage", quietly, which is the dangerous one |

The literature splits along the same line. A is the world of structured intermediate representations:
**QDMR/Break** (Wolfson et al., TACL 2020, a ~13-operator decomposition), semantic layers like
**Malloy** and **dbt MetricFlow** (which model an aggregate-of-an-aggregate with a *grain* type on
every node), and query IRs like **Apache Calcite**, **Ibis**, and **Substrait**. B is text-to-SQL:
**DIN-SQL** (Pourreza and Rafiei, 2023) reaches 85% execution accuracy on Spider but about 56% on
**BIRD**, and the best method solves roughly 6% of the enterprise-scale **Spider 2.0**. So a frontier
model nails a toy query while hiding silent errors, which is the regime where lineage matters most.

**The decision (D79 through D84).** B is the main path, behind the seam, with lineage recovered rather
than skipped. A strong model writes SQL for the questions the grammar can't reach, the SQL is checked
and run, and the answer ships only if the cells trace. I picked B over A because this system needs to
handle unknown questions without me forever hand-adding grammar nodes, and because lineage from SQL
turned out to be recoverable (section 4.5), which erased A's main advantage.

### 4.5 Lineage as a shared graph, not a copied-out list

Working out which cells fed an answer from arbitrary SQL is a solved research problem called
provenance by query rewriting. The theory is **semiring provenance** (Green, Karvounarakis, Tannen,
"Provenance Semirings", PODS 2007): tag the base rows, push the tags through joins and unions, and
each output ends up naming exactly which base cells combined to make it. Two implementations are
**ProvSQL** (Senellart et al., VLDB 2018, a Postgres extension that builds a compact provenance
circuit) and **GProM** (Glavic et al., which rewrites queries in any database).

The naive version, gluing `array_agg(__row_id)` through the group-by, works but balloons. A shared
sub-result (a national total built from 36 cells) gets *copied* into every row that divides by it, so
36 real cells become 1,332 references, a 37x blow-up. The fix is the same object the visualization
wants: a shared-node graph where the total is one node with 36 edges into it, and each node's cells
are worked out only when you ask. `sql_dag.py` parses the model's SQL with `sqlglot` into that graph.
CTEs and subqueries become shared nodes because the SQL already writes them once. That gives precise
lineage without the blow-up. It's ProvSQL's shape built with GProM's technique, on DuckDB.

**The decision (D82, D83).** Represent lineage as a shared-node operator graph, parsed from the
model's SQL, with each node's cells worked out on demand. Not a per-row list of row-ids built up
front, which balloons on any shared sub-result. The same graph then serves two things at once: the
exact lineage, and the click-to-cells visualization in the UI.

One honest limit ships with it. For rank or "which is the most" queries, the flat lineage is a safe
*superset* (all the rows the query scanned, not just the winner). It over-shows, never misses. Window
functions mark a node opaque, and then the path either falls back or refuses. It never shows a wrong
tree.

### 4.6 SQL safety is an AST check, not a prompt

Prompt-based safety fails eventually. The real boundary is a parser.

**The decision (D84).** Guard the model's SQL with an AST check, not a prompt. `sql_gate.py` uses
`sqlglot` to require: the root is a `SELECT` or CTE, no
`INSERT`/`UPDATE`/`DELETE`/`CREATE`/`PRAGMA`/`COPY`/`ATTACH` or file-reading functions, a single
statement, and every table and column present in the catalog (an unknown identifier means the query
isn't grounded, so it's refused). The DuckDB connection is opened read-only with external access
turned off. This is what production NL-to-SQL tools do: the parser is the boundary, the model is never
trusted.

### 4.7 Refusal has three outcomes, not two

**The decision (D20, D35).** Three outcomes, not two: answer, ask, or refuse. A system that refuses
everything is safe and useless, and over-refusing is a real failure. So the seven checks (section 6)
resolve to: answer when you're sure, ask about the one thing in doubt, and refuse only when the data
truly can't support it. The refusal text is written by us from the catalog. The model picks a reason
*code*, never the sentence. It once wrote "the table has data only up to 2023-24" about a file that
runs to 2025-26: the refusal was right, but the stated fact was false.

### 4.8 Cost is measured, not guessed

About $0.0002 per 6-question metered run (the cost-optimization eval), from three changes that stack,
each one metered rather than estimated. The broader 15-question `eval_e2e` runs ~$0.0015 — more of its
cases reach the model, and some route to the SQL escape hatch on a stronger model.

| | cost per 6-question run |
|---|---|
| baseline | $0.0041 |
| plus local-first cascade (Ollama handles about two-thirds) | $0.0029 |
| plus prompt caching (catalog in a stable prefix) | $0.0018 |
| plus reasoning tokens turned off | $0.00022 |

18x cheaper, same score. I would have missed the last one. I estimated $0.0007 a question, the real
figure was $0.0027, and metering showed reasoning tokens were 99% of the bill while I was about to
optimize the input side, which was already almost free. Measure cost. Don't estimate it.

### 4.9 Why not LangChain / LangGraph

I looked at both and chose the raw `openai`/`ollama` SDKs on purpose (D94). Those frameworks exist
to orchestrate agent loops: the model calls a tool, reads the result, decides, and goes again until
it is done. This system barely loops at all. The model does one small job, filling in a validated
JSON plan, and then ordinary Python takes over. It compiles the plan, runs it, and checks every
number against the source cell. The value, and the promise that the system never makes a number up,
lives in that ordinary code rather than in a framework.

The parts LangChain would hand me, I already have in a form I can defend line by line. Swapping
providers is a four-line entry in a registry dict (D22). Structured output with a retry is about
fifteen lines: validate the JSON, ask the model to fix it once, otherwise abstain. And reading
`usage` straight off the response is what showed me that reasoning tokens were 99% of the bill
(§4.8), the number that led to the 18x cost cut. A framework would have wrapped that away where I
never would have seen it. Putting a graph engine on top of a straight-line pipeline is more to
explain, not less to write.

I would reach for LangGraph the day this turns into a real multi-step agent: one that picks among
many workbooks, chains several questions together, searches across documents, and needs checkpoints
or a human in the loop. That is a different product from what this is, which is structured-data QA
with a hard traceability guarantee. At that point its state model would be worth the weight.

---

## 5. How lineage works

You record it at ingest, and you never rebuild it. `pd.read_excel()` throws away every cell address,
and you can't get it back: dropping blank rows, merged cells, filtering, and sorting each shift the
mapping in a different way, and the errors pile up. So the ingest walks the workbook cell by cell and
writes a receipt as it goes.

```
consumption   __row_id 18 | DELHI | NORTH | 2008-09 | ALL | 4072.53 | state
cell_map      18 . state   -> PT_Cons_Statewise!A11
              18 . region  -> PT_Cons_Statewise!A9     (the section header, nine rows up)
              18 . year    -> PT_Cons_Statewise!B8     (the column header)
              18 . value   -> PT_Cons_Statewise!B11
```

For the PPAC file that's 2,210 rows, 12,882 cell receipts, 352 formulas, and 18 citable notes. Look
at `region`: Delhi's row never actually says "NORTH". We forward-filled it from cell `A9` and carried
the *address* along with the value. Only a per-cell map can express that.

**Simple answers use a two-query trick.** One plan compiles into two statements that share the exact
same `WHERE`:

```sql
SELECT sum("value") AS v1        SELECT __row_id
FROM consumption                 FROM consumption
WHERE ... (identical)            WHERE ... (identical)
```

Swap the aggregate for `__row_id` and you get back exactly the rows that were summed. Not an estimate,
the same rows by construction. When the cited cells add up to the reported figure, that's proof the
citation list is complete, not just plausible.

**Hard answers use the operator graph** from section 4.5. For a question written as SQL, `sql_dag.py`
rebuilds the computation graph and works out each node's cells on demand. In the UI, cells that sit
next to each other collapse into ranges (`S10:S19`), so 36 cells become 5 clickable blocks. Click one
and the sheet opens with the whole range highlighted, the way you'd select it in Excel.

---

## 6. How refusal works

Seven checks, each placed at the earliest point its evidence still exists. Four run before any model,
so they're free.

| kind | example | caught |
|---|---|---|
| concept isn't there | "tax revenue" | before the model, by a catalog lookup |
| operation unsupported | "per capita", "median", "correlation" | before the model. Routed to the SQL path, or refused. |
| ambiguous entity | "Uttar" could be UP or Uttarakhand | before the model. It asks. |
| period with no anchor | "last year" | before the model. It asks. |
| scope not stated | "Bihar's share" with no year | after planning. It asks. |
| value not present | "2030-31" | at planning |
| empty result | valid query, zero rows | after running |

The biggest single source of bugs in the whole project was my own checks firing when they shouldn't,
five times, each one refusing a question it could have answered. That's why the gate tests are
two-sided: 28 inputs that must fire, and 41 that must stay quiet.

---

## 7. Evaluations

Eyeballing fails for a plain reason: a prompt or heuristic change is global, but eyeballing is local.
You fix one question and quietly break three. Every rule below was paid for by a specific bug, and the
journal grades judgment, meaning what went wrong and what caught it. They follow the "look at your
data, assert the value, test the thing that ships" school of LLM evals.

One small paid suite runs the real front door end to end. The rest are large, free, and
deterministic.

| suite | what it exercises | what it checks | cost |
|---|---|---|---|
| `eval_e2e.py` | the real `ask()` front door | the status, the citations, and the number (to a tolerance) | 15 model calls, ~$0.0015 |
| `eval_gates.py` | every refusal check, as a pure function | two-sided: 28 it must catch, 41 it must leave alone | free, instant |
| `eval_corpus.py` | ingest across 8 layouts | round-trip: the rows and every value's exact source cell | free |
| `eval_ingest.py` | ingest against a hand-checked spec | byte-identical row, receipt, and formula counts | free |
| `eval_record_qa.py` | Q&A over a record table | value and citation, checked against the source file | free |

The principles, each with the bug that bought it:

- **Check the number, not the status or shape.** A scoreboard that passes wrong answers is worse than
  none, because it turns an unknown into false confidence. `eval_e2e` once scored a wrong number 9/9
  by checking only status and citations (section 11). Now it checks the figure to a tolerance.
- **Test the real front door**, not an inner function. A check added upstream is invisible to a test
  that starts downstream, so the end-to-end suite calls the same entry point the API and UI call.
- **Two-sided gates.** Every check gets a list of inputs it must catch and a list it must ignore.
  Over-refusal caused five bugs, so there are now 41 "stay quiet" cases against 28 "fire" cases.
- **Make your own ground truth.** `eval_corpus.py` takes a clean table, folds it into a cross-tab (or
  keeps it as a record table, or adds blank rows and a footnote), then checks ingest gets the original
  rows back and points each value at the right cell. You know the answer before ingest runs. It's the
  same trick Auto-Tables uses to build 1.4M training pairs, and it caught two ingest bugs the two
  hand-picked files never hit.
- **A failing test is a disagreement between two parts of your thinking**, and which part is wrong is
  a question, not an assumption. Of the first six failures on this project, three were the code and
  three were the test.
- **Check against the original file**, not your own database. Every number in this README was checked
  by reading the actual cell in the actual `.xlsx`.
- **Cost is an eval too**, metered off real usage rather than estimated (section 4.8).

Where things stand: `eval_gates` 69/69, `eval_corpus` 8/8, `eval_ingest` 3/3, `eval_record_qa` 4/4,
`eval_e2e` about 14 or 15 out of 15 (one case is flaky, see section 8), and the frontend is `tsc`
clean.

What the evals don't cover yet, honestly: a two-sided answerable/unanswerable set with a
risk-coverage curve for the refusal layer, and running each question against several data variants to
catch SQL that matches by luck. Both are in section 9.

---

## 8. Limitations

Honest, with a rough severity for each. The full table is in
[docs/ROADMAP.md](docs/ROADMAP.md#ship-readiness).

| limitation | severity | detail |
|---|---|---|
| the hard-question path needs a strong model | expected | with no key, or a weak local model, hard questions refuse. They never answer wrong. The browser needs a key entered. |
| rank/max lineage over-shows | low | it cites every row the query scanned, not just the winner. A safe superset, never a miss. |
| the SQL graph models a single base table | medium | cross-table joins would need the semiring join rule, and window functions become opaque and refuse |
| planner non-determinism | medium | a strong model sometimes mis-plans a share as a group-by (`v1='BIHAR'`). No retry or self-consistency yet, which is the one flaky e2e case. |
| the SQL path is scalar-only | medium | "list the top 5" through SQL is deferred. The typed path still handles the common lists and ranks. |
| stacked headers get combined, not split | low | the Union Budget's year-by-estimate-type header is merged into one unique period (usable), not split into two dimensions |
| an onboarding label quirk | cosmetic | a record file's geometry line reads "names in column E", where E is the last identifier rather than the entity |
| multiple tables or transposed sheets | out of scope | detected as low-confidence and flagged, not parsed, which matches the "no automatic structure detection" stance in 4.2 |

The pattern holds: where the system is unsure, it asks or refuses out loud. It doesn't hand you a
plausible wrong answer. That's the property the whole design pays for.

---

## 9. Future improvements

1. **Generalize the operator-graph lineage.** A full `sqlglot` AST turned into a semiring-annotated
   graph (Green et al., GProM-style rewriting) would take precise lineage from grouped queries to any
   query, including multi-table joins, and turn rank/max lineage from a superset into the exact set.
2. **Self-consistency escalation.** Run the model a few times. If the runs disagree across independent
   data variants, ask a clarifying question. This is the finding that about 1 in 8 "consistent"
   text-to-SQL answers is still wrong. It catches failures the static checks can't see, and it would
   fix the flaky-planner case.
3. **The `measure_name` refactor (parked as "form B").** One long internal shape unifies cross-tab and
   record tables downstream, so "which of these three metrics?" comes for free instead of as a special
   case.
4. **State-history awareness.** Telangana is zero before 2014-15 and Andhra Pradesh drops 41% that
   year. That's the state split, not a demand collapse. A state-lineage table would let comparisons
   across it refuse instead of mislead.
5. **Follow the external `VLOOKUP`.** FY2025-26 values resolve into a workbook we don't have, so the
   system should say so rather than cite a cell whose formula points somewhere else.
6. **The other two Coreworks outputs.** Excel Analysis and Report are more renderers of the same
   `Answer` objects. A Report is `weasyprint` over a Jinja template, and nothing upstream changes.

---

## 10. How the system grew

The project grew in five stretches. Each is a chapter in [docs/DECISIONS.md](docs/DECISIONS.md) and
[docs/ROADMAP.md](docs/ROADMAP.md), and each came from a specific pushback or a measured failure.

**One: one file, done deeply (D1 to D57).** A single hardcoded workbook, the typed-plan seam, the cell
map, the seven refusal checks, cheap-first planning, derived values as lineage trees, and the eval
discipline. This is where the core idea and every rule got paid for in bugs.

**Two: generality (D58 to D65).** The pushback: "this is hardcoded to one dataset." Fair. Ingest split
into a universal reader (`probe`) and a per-file confirmed spec (`propose`, `spec`). A layout
classifier (4.3) so record tables stop getting melted. CSV and TSV through one loader. And every
file-specific constant removed, so nothing carries over from one file to another. The round-trip
corpus proves it.

**Three: the product surface (D63 to D75).** The pushback: "make it feel AI-native." A React and
FastAPI client replacing Streamlit, on the same `Answer` contract. Designed PPTX decks.
Story-from-data and brief-driven deck authoring, where an agent plans the narrative and the pipeline
supplies every cited number. A delete for workbooks. And click-a-reference-to-open-the-sheet so you
can check lineage yourself.

**Four: hard questions (D79 to D84).** The pushback: "how do we handle deeper questions and stay
generic?" The A-versus-B study (4.4), the shared-node graph (4.5), the SQL-to-operator-graph parser,
and the escape hatch wired into the product behind the seam. Hard questions now answer, through
checked SQL, and still trace.

**Five: polish and ship (D85 to D86).** Statistics like `median` and `stddev` routed to the SQL path
instead of the planner quietly turning them into `avg`. Cells collapsed into highlighted ranges.
Legacy Streamlit removed and the dependency list fixed. And the bug journal and ship-readiness docs
brought fully up to date.

The one thing that never moved through all of it: the seam. New models, new question shapes, new
renderers, and a whole second answer path all slotted in behind a checked plan and an `Answer` object.

---

## 11. Appendix

### Impressions of the Coreworks product

I spent time with Coreworks before building this. You drop in a data file and it turns it into one of
three things: a presentation, an Excel analysis, or a report, either from a short prompt or from a
template. The decks are the striking part. They come out properly designed, with themed layouts, big
KPI tiles, and charts, and there's a gallery of ready-made ones (Customer QBR, SEO Report, Sales
Review, an investment memo). The use-cases page frames it well: the data going in, the prompt that
shaped it, and the narrative that came out, with "100% traceable" as the headline and roughly seven
minutes from a raw export to a finished deck.

Two things stuck with me and shaped how I built this. First, that "traceable" claim is the whole
point, not a footnote. A generated deck full of confident numbers is worthless if you can't say where
each one came from, which is exactly the property this take-home asks for and the one I spent most of
my time on. Second, the three outputs are clearly renderers of one underlying analysis, not three
separate products. That's the shape the assignment calls out (a slide deck should be a natural next
step, not a rewrite), and it's why my pipeline ends in a structured `Answer` object that a deck, a
report, or a UI all read from. What I built is the analysis-and-lineage engine that would sit under a
product like this: it makes the numbers defensible, and Coreworks makes them beautiful.

### The example files

The engine reads any `.xlsx` or `.csv`, and carries nothing from one file to the next. The three files
below are picked so that between them they stress the shapes that break naive systems. Breadth beyond
them is proven by the synthetic corpus (section 7), not by these three.

| file | shape | what it stresses |
|---|---|---|
| PPAC POL Consumption (Min. of Petroleum) | cross-tab, 3 sheets, 2,210 rows | real formulas (`SUM`, an external `VLOOKUP`), two aggregate traps, footnotes inside the data, confusable names |
| BananaPatterns Climbing (stock breakouts) | tidy record table, one row per stock | the opposite shape (melting it was a real bug, 4.3), many measure columns, no time axis |
| Union Budget at a Glance | cross-tab with stacked bilingual headers | year-over-estimate-type headers in Hindi and English, `..` as missing markers, data starting in column D |

PPAC earns its traps because they show why lineage isn't optional. A naive `SUM` over FY2025-26 gives
688,573 against a true 223,480, a 3.08x inflation from the `Region Total` rows. And because `MS` and
`HSD` are subsets of `ALL`, an unfiltered Delhi FY2015-16 reads 7,406.53 instead of the correct
4,996.70. A system that can't show which cells it summed can't be trusted to have dodged either trap.
One more reason it earned its place: PPAC is Indian, so I can smell a wrong number. A wrong
Maharashtra figure sets off an alarm that a wrong Ohio figure wouldn't. Domain knowledge is a
debugging tool, and it caught the worst bug in the project.

### The worst bug

"Which region used the least diesel in 2020-21?" answered SOUTH, 15.98. South India has Tamil Nadu and
Karnataka in it, so 15.98 is absurd. It's Lakshadweep's figure. The plan used `min(value) GROUP BY
region`, which finds the region *containing* the smallest row, not the region with the smallest total.
The right answer is NORTH EAST, 1,775.69.

It passed every check. Coverage saw all the filters. The compiler saw valid columns. The lineage check
passed, because for `min` the rule is "the answer is one of the cited values", and 15.98 really was
one. And my eval scored it 9/9, because it checked status and citations but never the number.

Three lessons, in order:

1. A scoreboard that passes wrong answers is worse than none. The suite now checks the value.
2. A human who knew the domain caught it, which is the whole "why this dataset" argument paying off.
3. `min` and `max` with `group_by` and `limit` are now rejected at compile time. Fix it in structure
   first, prompt second.

### Where the thinking is written down

| doc | holds |
|---|---|
| [docs/DECISIONS.md](docs/DECISIONS.md) | 86 decisions: what was chosen, what was rejected, and why |
| [docs/BUGS.md](docs/BUGS.md) | the bug journal: 29 lessons, each ending with what caught it |
| [docs/ROADMAP.md](docs/ROADMAP.md) | the journey, the pushbacks that changed the design, and ship-readiness |

Every number in this README was checked against the original `.xlsx`, not against our own database.
