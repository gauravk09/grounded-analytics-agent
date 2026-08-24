# DECISIONS

Finalised decisions only. Each one is something a reviewer may ask "why this over Y?" about.
The reasoning trail, rejected options and open questions live in [ROADMAP.md](ROADMAP.md).

Status key: **LOCKED** = decided and built · **AGREED** = decided, not yet built · **PARKED** = deferred on purpose

---

## D1 — The LLM never produces a number
**LOCKED**

The model emits a sentence with holes (`"Delhi consumed {v1} thousand tonnes in {v2}."`).
The engine fills the holes. The model has no channel through which a digit can reach the user.

**Why:** makes a hallucinated number *unrepresentable*, not merely unlikely. This is the
difference between "we check the model's numbers" and "the model cannot emit numbers" — and it is
the single line the whole architecture is built to defend.

**Rejected:** letting the model write prose containing figures and validating them afterwards.
Validation after the fact is a filter; this is a structural guarantee.

---

## D2 — Architecture: model plans, engine computes
**LOCKED**

Pipeline: `question → catalog → LLM plan → validate → DuckDB → cell lookup → Answer object`.

**Rejected — RAG over spreadsheet chunks.** The model would do the arithmetic, which is
unverifiable, and "lineage" degrades to "here are the chunks I looked at" rather than a cell.

**Rejected — LLM writes pandas code, we `exec` it.** Arbitrary Python cannot be statically
proven to touch only allowed columns, and you cannot recover which rows produced the answer.

**Chosen — a restricted, validated query plan.** Validatable before execution, and row-level
provenance falls out naturally (same `WHERE`, project `__row_id` instead of the aggregate).

---

## D3 — Plan format: constrained JSON spec, compiled to SQL by us
**AGREED**

The model fills a narrow spec (`table`, `metric`, `filters`, `group_by`). Our code compiles it
to SQL.

**Why:** `llama3.2:3b` cannot write reliable SQL, but it can fill slots. And a spec is
validatable *by construction* — there is no generated string to parse and second-guess.

**Rejected — model writes raw SQL.** Viable with a frontier model, too fragile with a 3B local one.

---

## D4 — Planner LLM: local Ollama first, hybrid later
**AGREED**

`llama3.2:3b` via Ollama, behind a `Planner` protocol so a `ClaudePlanner` is a config swap.

**Why:** offline demo, no API key at the interview. Interface keeps the upgrade cheap.

---

## D5 — Refusal is a first-class output, not an error path
**AGREED**

The planner returns one of two shapes: a query plan, or `{"kind": "abstain", "reason": ...}`.

**Why:** if refusal lives in `except`, it fires when the *code* breaks. The dangerous cases don't
break anything — a wrong answer looks exactly like a right one. Refusal must be something the
system *chooses*, on equal footing with answering.

**Corollary:** answerability is decided against the catalog, not by asking the model whether it
can answer. Self-assessment is precisely the faculty an LLM lacks.

---

## D6 — Lineage is captured at ingest, never reconstructed
**LOCKED**

Every value written to the tidy table records its source cell in the same loop iteration that
read it.

**Why:** lineage is a fact about *how the file was read*, not a property of the data. The only
entity that ever knew "Delhi's figure came from `B11`" is the loop that read `B11`. Offset
arithmetic (`sheet_row = df_index + 9`) breaks on blank rows, merged cells, filtering and sorting
— and those errors compound rather than cancel.

**Rejected — `pd.read_excel()` then map back.** Not recoverable, at any price.

---

## D7 — Two-pass workbook read
**LOCKED**

`load_workbook(data_only=True)` for cached values, `data_only=False` for formula strings; read
the same cell from both.

**Why:** costs three lines, buys `"B20, whose formula is =SUM(B10:B19)"` instead of just `"B20"`.
352 formulas captured from this workbook.

---

## D8 — Cleaning is non-destructive: transform into the table, original into the receipt
**LOCKED**

`cell_map.raw_value` holds what the cell literally contained before cleaning.

**Why:** makes "you changed this value — show me the original" answerable. The brief's
"messy cells" hint signals they intend to ask.

---

## D9 — Aggregate rows are kept and tagged, never dropped
**LOCKED**

`row_kind ∈ {state, region_total, all_india}`.

**Why:** `ALL INDIA TOTAL` is a real published figure; deleting it would make a legitimate
question unanswerable. Tagging turns the trap into a filter instead of a deletion.

**Enforcement — in the compiler, not the prompt.** Any aggregation without an explicit
`row_kind` filter gets `WHERE row_kind = 'state'` added automatically.

> Never fix a trap by asking the model nicely. Fix it where it is mechanically impossible to get wrong.

Measured: naive `SUM` over FY2025-26 gives 688,573 vs the true 223,480 — a **3.08× inflation**.

---

## D10 — When you propagate a value, propagate its address
**LOCKED**

The region forward-fill carries both `region` and `region_a1`.

**Why:** Delhi's region came from `A9` — the section header nine rows up, not Delhi's own row.
Only a per-*cell* map can express that; a per-row lookup cannot.

---

## D11 — Titles and footnotes are captured as citable context
**LOCKED**

`sheet_notes(sheet, a1, text)` holds rows 1, 5, 6, 7, 56, 57 of each sheet.

**Why:** the region totals do not sum to `ALL INDIA TOTAL` (gap of 18,132 in FY2025-26). The
explanation is literally cell `A57`: *"Total Fig. includes IMPORTS & SEZ STATE"*. The system can
answer "why don't these add up?" by pointing at a cell — something no model cleverness could do.

---

## D12 — Dataset: PPAC State-wise POL Consumption
**LOCKED**

Ministry of Petroleum (PPAC), state-wise petroleum sales, FY2008-09 → FY2025-26. 100 KB, 3 sheets.
`data/ppac_statewise_sales.xlsx`

**Why it wins:** the only candidate hitting every requirement at once — real `SUM` and `VLOOKUP`
formulas, title/footnote rows bracketing the data, five duplicate `Region Total` labels, a
reconciliation gap explained by a footnote cell, confusable state names
(`ANDHRA PRADESH`/`ARUNACHAL PRADESH`), the 2014 Telangana bifurcation trap, and a natural
unanswerable question (the file has volumes only — no prices, no revenue, no tax).

**Also: Indian domain knowledge is a debugging tool.** A wrong Maharashtra figure sets off an
alarm that a wrong Ohio figure would not.

**Rejected:** EIA fuel taxes (equally good, but US); Union Budget at a Glance (best mess and most
familiar, but zero formulas); Annual Financial Statement (worst formatting, but `Sheet1..Sheet9`
and no formulas); Superstore and Global Superstore (great name-ambiguity, but clean — ingest would
look trivial); Census MARTS (true 3-way join, no formulas).

---

## D13 — Reshape: unpivot to long, all three sheets into one table
**LOCKED**

`consumption(state, region, year, product, value, row_kind)` — 2,210 rows.

**Why:** tiny catalog for a 3B model; year-over-year and petrol-vs-diesel both become simple
`GROUP BY`s. And the unpivot is the hardest lineage case — each output row takes its value from a
*different column* of the same input row — so surviving it proves the receipt book works.

**Rejected:** one table per sheet (24-entry catalog, a 3B model picks wrong constantly); keeping
the wide format (year-over-year needs arithmetic across 18 columns).

---

## D14 — Lineage depth: cell address + formula string. No dependency graph.
**AGREED**

Citations carry `sheet`, `a1`, and the formula if present. We do **not** recursively resolve
`=SUM(B10:B19)` into its ten precedent cells.

**Why:** the marginal interview value is small and cross-sheet reference resolution is easy to get
subtly wrong.

---

## D15 — Interface: Streamlit
**AGREED**

Click a number → its source cells expand.

**Why:** lineage is a *visual* claim. Clicking is more convincing than printing.

---

## D16 — Generality: universal reader + per-file spec
**PARKED** — architecture agreed, refactor deferred (~1 hour)

Ingest constants (`HEADER_ROW = 8`, …) move out of `ingest.py` into `specs/ppac.yaml`.
`ingest.py` becomes generic: it reads *any* sheet given a spec.

**The principle:** separate the **reading** (universal, mechanical — walk every cell, record
address/value/formula) from the **interpretation** (per-file, must be confirmed — which row is the
header, which rows are aggregates).

**Why interpretation can never be universal:** nothing in the file says whether `A9 = "REGION -
NORTH"` is a section header or a state name. A spreadsheet is a grid of cells, not a table;
humans infer structure from layout and domain knowledge. Silently guessing wrong makes every
downstream number *and* every citation wrong — confidently. That is hallucination relocated into
the ingest layer, where nobody is looking for it.

**How a new file gets a spec (three tiers):**
1. **Heuristics** — header row = first mostly-text distinct row followed by numeric rows; aggregate
   rows = label contains `total`/`sum`/`subtotal`. Free, catches the easy cases.
2. **LLM proposes the layout** — show it the top-left ~30×15 raw grid, get back
   `{header_row, label_column, aggregate_rows, orientation, confidence}`. Legitimate because it is
   a *perception* task with a small checkable answer ("which row is the header?" → look at row 8),
   unlike arithmetic which cannot be checked without redoing it.
3. **Human confirms once**, then the spec is frozen to a file. Ingest is 100% deterministic
   thereafter; the model never runs again for that file.

Same shape as the rest of the system, one layer down: **model proposes, engine or human disposes.**
And abstention applies here too — if heuristics and model disagree, ask rather than guess.

---

## D17 — Catalog holds structure, never values
**AGREED**

The catalog exposes column names, types, null counts, distinct **counts**, and the distinct
**labels** where cardinality is low (< ~50). It does **not** expose mean, median, mode, min, max,
percentiles, or sample rows.

**Why:** a mean is a number. Once a number is in the model's context it can be echoed into an
answer, and that number has no citation because nothing computed it through the engine. That
reopens the hole D1 exists to close.

> Structure goes in the catalog. Values go through the engine.

`COUNT(DISTINCT state) = 36` is a fact about the *shape*.
`AVG(value) = 4821` is a fact about the *contents*. Only one of those can be misquoted.

**Rejected — a data-science style profile (mean/median/mode/distribution) in the prompt.** The
underlying need is real, but it is a set of *pre-computed answers*, not prompt context. It slots
in later as a "profile this dataset" mode emitting normal Answer objects with full citations —
which is strictly better than the model reading figures off a fact sheet.

**Accepted cost:** without ranges in the catalog the model may generate a filter that matches
nothing (`year = '2030-31'`). That returns zero rows and the system abstains — one of the four
refusal cases, and correct behaviour rather than a bug.

**Generated, never hand-written:** `catalog.py` builds it from DuckDB at startup (`DESCRIBE`,
`COUNT(DISTINCT …)`, `SELECT DISTINCT …`), so it cannot go stale when the data changes.

---

## D18 — Synonyms via an explicit alias allowlist. No embedding similarity.
**LOCKED**

Each catalog column carries a `description` and an `aliases` list. The planner maps the user's
words to a column; the validator rejects anything outside `catalog.column_names`.

Matching order: exact → normalised exact → explicit alias → **abstain**.

**Why not embeddings / vector similarity:** cosine similarity *always* returns a nearest
neighbour. "Tax revenue" is genuinely close to "consumption value" — both are quantitative
business metrics — and no threshold reliably separates them, only a knob tuned until the demo
looks right.

> **Embedding similarity cannot abstain.** There is always a nearest neighbour.

**The trap this avoids:** "petrol" → `MS` is safe. "fuel sales" is not — volume or revenue? Our
file has volume only, so silently mapping it to `value` produces a confident, fully-cited, *wrong*
answer. Same failure as churn → `retention_pct`.

**The semantic layer is hand-authored, and that is the industry answer** (dbt, LookML, Cube all
work this way). Some of it is derived from the workbook: units from cell `A6`, the MS/HSD
expansions from the sheet names. Column descriptions also state what the file does *not* contain
("no prices, revenue or tax"), which helps the planner choose `abstain`.

---

## D19 — Total rows carry no state name
**LOCKED**

`state` is set only when `row_kind = 'state'`; `region_total` and `all_india` rows have
`state = NULL` and are identified by `region` + `row_kind`.

**Why:** found by reading the generated catalog, which listed `Region Total` among the state
values — meaning the planner could have filtered `state = 'Region Total'`.

**Worth noting as a technique:** the catalog is a mirror of the data. Generating it surfaced an
ingest bug that reading `ingest.py` did not.

`row_kind` still cites the label cell it was read from (e.g. `A20`), so a total row's provenance
is complete even though it has no state.

---

## D20 — Three-way matching: map, ask, or abstain
**AGREED** — supersedes the hand-authored half of D18

D18 said aliases are an explicit allowlist. True for *this* file, wrong as a product: a user
uploads their own spreadsheet and nobody is standing by to write an alias list.

So aliases are **generated at upload and confirmed lazily**.

### Generation
At upload the model proposes `description` + `aliases` per column from the structural signals
already available: column names, the low-cardinality labels, sheet titles, and the units cell
(here, `A6` = `('000 Metric Tonnes)`). Legitimate for the same reason as D16 — a language task
with a small, checkable output, not arithmetic.

### Matching is three-way, not two
| candidates | action |
|---|---|
| exactly one, confident | **map** — and surface the mapping in the answer so it stays auditable |
| two or more | **ask one clarifying question** |
| zero | **abstain**, and say what the file *does* contain |

Previously "fuel sales" abstained. Now it asks:

> *By "sales" do you mean volume consumed (thousand tonnes)? This file has no revenue or price data.*

### Why asking beats both alternatives
- **Better than guessing:** a clarifying question converts an unverifiable guess into a verified
  fact for the cost of one round trip.
- **Better than refusing:** refusing when one question would resolve it is over-refusal — the
  failure mode named in Lesson 0.5. A system that refuses everything is trivially safe and useless.

### Confirmations are remembered
The user's answer is written into that dataset's alias list. Asked once, not every time. The
semantic layer becomes **learned**, not authored — which is what makes this work without a human
curator per file.

### Guard against over-asking
Only ask when the choice changes the answer. If two candidate columns would produce the same
result, pick either and say which.

### Shape
Same as D16 and D2, one layer over: **the model proposes the question; the user's reply is ground
truth.** The model never resolves the ambiguity by itself.

---

## D21 — Annotations move to a generated file
**PARKED** — design agreed, build deferred until the pipeline is end-to-end

The `ANNOTATIONS` dict hardcoded in `catalog.py` is a placeholder for D20's generated semantic
layer. Three changes when it lands:

1. **Dict → file.** `specs/<dataset>.annotations.json`, loaded by `catalog.py`. Missing file is
   not an error — the catalog degrades to bare names.
2. **New `annotate.py`,** run once after ingest. Sends the model *structure only* — column names,
   types, distinct counts, the low-cardinality labels, and the sheet note cells (here `A7` title,
   `A6` units). No data rows; D17 applies to the annotator too. Returns
   `{column: {description, aliases, confidence}}`.
3. **Low confidence does not block.** The file is written regardless, uncertain entries flagged.
   The user is never made to review a form before their first question; doubt surfaces later at
   query time as the D20 clarifying question.

**Why it is safe:** the model proposes *labels for columns* from structure alone. A bad alias
costs a worse mapping, never a wrong number — the whitelist still rejects invented column names
and DuckDB still computes every figure. The output is plain JSON a user can edit.

---

## D22 — The seam is the validated plan, not the model's output format
**AGREED** — build for the frontier model we may use tomorrow, not only the 3B one we have today

D3 chose a constrained JSON form because `llama3.2:3b` cannot write reliable SQL. That is a
statement about today's model, and must not become a ceiling on the architecture.

So the interface downstream of the planner is a **validated `Plan` object**, and there can be more
than one way to produce it:

```
weak model   → fills a form   ─┐
                               ├→ [validated Plan] → compile → execute → verify → Answer
strong model → writes SQL     ─┘   (parsed with sqlglot, checked against catalog.column_names)
```

Compile, execution, the provenance query, the refusal gates, the Answer object and every renderer
are identical in both paths. Upgrading the model changes **one class**, not the pipeline.

**Two independent swaps, deliberately kept separate:**
1. *Which model* — the `Planner` protocol (D4). `OllamaPlanner` → `ClaudePlanner`.
2. *Which plan format* — a second implementation emitting SQL, parsed into the same `Plan`.

**The constant is the validator.** Whoever writes the query, the same checks run: single
statement, SELECT-only, every table and column on the whitelist, `row_kind` guard applied (D9).
That is what makes it safe to let a stronger model write something richer later.

**Accepted limit today:** the form cannot express window functions, subqueries or arbitrary joins.
Correct for now — every query it *can* express is one we can validate and trace. The SQL path is
where that ceiling lifts, without loosening any guarantee.

---

## D23 — Planner is DeepSeek, with local Ollama as automatic fallback
**LOCKED**

`get_planner()` returns `DeepSeekPlanner` when `DEEPSEEK_API_KEY` is set, `OllamaPlanner`
otherwise. This is D4's hybrid, arriving earlier than planned.

**Why the switch:** `llama3.2:3b` scored 4/6 on the eval set and was in prompt whack-a-mole —
each fix broke a previously passing case. It was reliable at *deciding* (query vs abstain, 2/2)
and unreliable at *composing* four fields at once, silently dropping `filters` as the prompt grew.
DeepSeek scored 5/6 on the first run with no prompt changes.

**Key handling:** read from `DEEPSEEK_API_KEY` via `.env` (gitignored). Never hardcoded, logged,
printed, or included in a prompt. `.env.example` is committed and carries the variable name only.

**Cost:** ~1,500 input / ~200 output tokens per question ≈ $0.0007. The 6-case eval ≈ $0.004.

**Not a loosening of any guarantee.** The failure policy is unchanged — validate with Pydantic,
one repair retry, then abstain. DeepSeek offers JSON-object mode but not JSON-Schema constrained
decoding, so the schema moves into the prompt and Pydantic remains the sole enforcer.

---

## D24 — `product = 'ALL'` is a compiler default, like `row_kind`
**AGREED** — to be built in `compile.py`

`MS` and `HSD` are subsets of `ALL`. Aggregating without a product filter double-counts.

Measured, Delhi FY2015-16: no filter = 7,406.53 vs `product = 'ALL'` = 4,996.70 — **48% inflation**.

Any aggregation whose plan contains no `product` filter gets `product = 'ALL'` injected.

**This is D9's twin on a different axis.** D9 was an aggregate hiding in *rows*
(`Region Total`, `ALL INDIA TOTAL`); this is an aggregate hiding in a *column value*. Same class
of bug, same fix: enforce it in the compiler, never in the prompt.

**Found by the eval harness, not by reading the code.** Worth noting as a technique — the
scoreboard caught a data trap that had survived both my inspection of the workbook and my design
of ingest.

---

## D25 — Cost-tiered planner cascade, cheapest first
**LOCKED**

`CascadePlanner` tries planners in order — local Ollama, then DeepSeek — escalating only when a
tier fails. Measured on the 6-question eval: **Ollama handles 4, DeepSeek 2**. Cost per full run
drops ~65%, from ~$0.004 to ~$0.0014.

Three things had to be true for tiering to work at all:

**1. `PlanFailure` is distinct from `Abstain`.**
`PlanFailure` = the model could not produce a valid plan → escalate.
`Abstain` = the *data* cannot answer the question → stop. Escalating a legitimate refusal would
just pay a stronger model to reach the same conclusion.

**2. A mechanical failure signal — `coverage_gaps()`.**
Schema validation cannot see a semantically wrong plan, and a small model's characteristic
failure is *valid JSON with filters silently dropped*. So: **if the question names a value that
exists in the catalog, the plan must filter on it.** Word-boundary matched, over catalog labels
and value aliases.

**3. The gate must not demand what the compiler supplies.**
`product` and `row_kind` are injected by the compiler (D9, D24), so the gate ignores them when
the question implies only their default value.

**Bugs hit, both worth keeping:**
- `"east" in "least"` — substring matching without word boundaries. Fixed with
  `(?<![a-z0-9])term(?![a-z0-9])`.
- `questions.yaml` demanded `product: ALL` from the *planner* while D24 assigns it to the
  *compiler*. The failing test was wrong, not the code. A scoreboard is only as good as its
  expectations.

**Value-level synonyms** were added to the catalog for this (`petrol → MS`, `diesel → HSD`), which
also strengthens D18's alias allowlist at the value layer rather than only the column layer.

---

## D26 — Model tier and cost: measured, not estimated
**LOCKED**

Model is **`deepseek-v4-flash`** (cheapest paid tier). `deepseek-chat` no longer exists as a real
model id — the API now serves `deepseek-v4-flash`, `deepseek-v4-pro`,
`deepseek-v4-flash-vision-exp`.

`DeepSeekPlanner._bill()` reads `usage` off every response and accumulates real spend, reported
at the end of each eval run. **This is the decision, not a detail** — the estimate was 4x wrong
and wrong in a direction that would never have been noticed.

### Measured cost of one 6-question eval run

| stage | cost | what changed |
|---|---|---|
| baseline | $0.0041 | — |
| + Ollama cascade (D25) | $0.0029 | 4 of 6 questions never reach the API |
| + prompt caching | $0.0018 | catalog in a stable prompt prefix |
| **+ reasoning disabled** | **$0.00022** | reasoning tokens were **99%** of the bill |

**~18x cheaper, still 6/6.**

### Prompt caching is a message-ordering decision
Cached input is $0.007/M vs $0.22/M fresh — **31x cheaper**. The system prompt plus catalog
(~1,900 tokens) is byte-identical on every call and sits *before* the varying question, so it
caches. Measured: 1,920 cached + 102 fresh per call.

### Reasoning off by default
`extra_body={"thinking": {"type": "disabled"}}`. The model was emitting ~4,000 reasoning tokens
before a ~150-token plan; output is billed at $0.66/M, so this dominated everything. For
slot-filling with three worked examples in the prompt, the thinking bought nothing — and the eval
proves it, because the score did not move. `DeepSeekPlanner(thinking=True)` re-enables it if a
harder plan format ever needs it.

### Pricing reference
Off-peak USD/M tokens. Peak (01:00–04:00 and 06:00–10:00 UTC) is **2x**.

| model | cache hit | cache miss | output |
|---|---|---|---|
| v4-flash | $0.007 | $0.22 | $0.66 |
| v4-pro | $0.022 | $0.66 | $1.98 |

**Lesson worth keeping:** without metering, the obvious optimisation would have been shortening
the catalog — the input side, which was already 99% free.

---

## D27 — Plan-echo: when a failure can't be detected, make it visible
**AGREED** — from Gaurav's pushback that `coverage_gaps` is overfit to this dataset

### The pushback
`coverage_gaps` (D25) leans on hand-written `value_aliases` and `COMPILER_DEFAULTS`. On a fresh
upload those are empty, so the gate weakens to literal label matching only.

### What is actually generic
Generic with zero config: the principle (question names a catalog value → the plan must filter on
it), word-boundary matching, skipping high-cardinality columns (falls out of D17), skipping
abstains, and **literal label matching** — a `city` column containing `MUMBAI` catches "Mumbai"
with no configuration. Aliases only add coverage where labels are *codes* rather than words
(MS, HSD), and D21 already generates those at upload.

### The larger, conceded weakness
Even fully generic, the gate detects only **one** failure class — a dropped entity. It is blind to
wrong aggregation (`avg` for `sum`), wrong operator, dropped ranges ("between 2015 and 2020"
collapsed to one year), and misread intent (*growth* answered as *level*). Each produces perfect
coverage and a wrong answer. The gate was oversold as a correctness check.

### The genuinely generic detector we are not building yet
**Self-consistency** — run the cheap local model 3x with variation; disagreement → escalate. Zero
dataset knowledge, catches every failure class above, and local calls are free. Parked only for
demo latency.

**Rejected — LLM-as-judge** ("does this plan answer this question?"). Reintroduces an
unverifiable judgment, and a cheap judge fails on the same questions the cheap planner does.

### The decision: plan-echo
Every answer renders the plan back in plain English beside the number:

> Gujarat consumed **11,824.5** thousand tonnes of diesel in 2019-20.
> *Computed as: sum of value where state = GUJARAT, product = HSD, year = 2019-20, row_kind = state*

Fully generic — it is just rendering the `Plan`. No config, works on any upload, and it catches
*every* failure class because the person who asked the question is reading it. Same move as D20:
when the machine cannot be sure, surface it to the human.

### Resulting roles
- `coverage_gaps` — a cheap **routing hint** for the cascade, not a correctness guarantee
- **plan-echo** — the actual correctness surface, and needed for the Streamlit UI regardless
- self-consistency — parked

---

## D28 — The coverage gate must apply to every planner, not only the cascade
**AGREED** — bug found while walking the code

`get_planner()` returns a bare `OllamaPlanner` when no API key is present, and `coverage_gaps` is
only called inside `CascadePlanner`. So with no key the quality gate does not run at all.

The gate is a quality check, not a routing detail. Fix: always wrap in `CascadePlanner`, even
with a single tier, so a coverage failure produces a clean `Abstain` rather than a silently
under-filtered answer.

---

## D29 — The provenance query is the value query with one line changed
**LOCKED**

`compile_plan()` returns both statements from one plan, sharing a single `_where` string:

```sql
SELECT sum("value") AS v1   │  SELECT __row_id
FROM consumption            │  FROM consumption
WHERE ... (identical)       │  WHERE ... (identical)
```

They cannot drift apart, because there is only one WHERE — not a rebuild, not copied logic, the
same characters.

**Verified end to end:** "Gujarat's diesel consumption in 2019-20" → 5,607.58 → `__row_id` 1993 →
`PT_Cons_Statewise HSD!M42`. Opening the original `.xlsx` independently and reading `M42` gives
`5607.584527746732` — exact match. The check was against the source file, not against our own
warehouse.

**Grouped provenance must be narrowed to the group returned.** "Which region used the most diesel
in 2024-25?" → NORTH, 27,551.84 → provenance with `{"region": "NORTH"}` → 10 rows → cells
`HSD!R10`–`R19`, which sum to exactly 27,551.84. Without narrowing, the WHERE matches all 36
states and the citations would be technically "rows scanned" and completely wrong as an answer to
"where did this number come from?".

`provenance_sql()` **raises** when a grouped plan is called without `group_values`, rather than
returning over-broad citations quietly. An interface that can be misused silently will be — that
cost real time twice already (this, and the eval's trace ordering).

**Falls out for free — a completeness check on lineage:** the cited cells must sum to the reported
number. If provenance missed a row or included an extra, the sums disagree. Pure arithmetic, and a
hard test that citations are complete rather than merely plausible.

---

## D30 — Three levels of trust, three treatments, in the SQL string
**LOCKED**

| part | treatment | safe because |
|---|---|---|
| column name | interpolated, double-quoted | passed `catalog.column_names` whitelist |
| operator | interpolated | Pydantic `Literal` — six symbols, nothing else reachable |
| `limit` | interpolated via `int()` | Pydantic types it `int` |
| **value** | **bound parameter `?`** | unconstrained free text — never touches the string |

> Interpolate only what is provably constrained. Everything else is a parameter.

**Values are parameterised even though the user never writes SQL.** The chain is
user question → model prompt → model output → SQL, so the user controls the input to the model
and can influence what it emits. Prompt injection becomes SQL injection with the model as the
delivery mechanism.

**And the everyday bug is the same bug:** `JAMMU & KASHMIR` and any name containing an apostrophe
break string-gluing without any attacker involved. Parameters fix the ordinary case; the security
property comes free.

`where` and `params` are built from one list in one iteration and travel together inside
`Compiled`, so the Nth `?` matches the Nth parameter by construction — never by convention.

---

## D31 — Slot order is a contract between the prompt and the compiler
**LOCKED** — with a known fragility

The SELECT list is group-by columns first, metric last, so `{v1}` is the grouped label and `{v2}`
is the number — matching the worked examples in the planner prompt.

`ORDER BY {len(select)}` sorts by **position**, which is always the metric because the metric is
appended last. Positional ordering is used because `ORDER BY sum("value")` and `ORDER BY v2` are
not portable.

**The fragility, stated plainly:** nothing enforces this. It is a convention held in two files —
the prompt's examples and the compiler's SELECT order. Reorder the examples and slots fill
backwards ("5607.58 consumed the most petrol, at TAMIL NADU thousand tonnes") with no error.

**The stronger version, not built:** have the planner name its slots explicitly
(`"slots": ["state", "metric"]`) instead of relying on position. Candidate for "what I'd do with
another week".

---

## D32 — The Answer keeps narration and numbers separate to the very end
**LOCKED**

`execute()` returns an `Answer` holding a narration *template* plus a dict of `Value` objects —
never a finished sentence. `Answer.text()` is the only place a number joins a sentence, and it is
called by a renderer, last.

**Why:** a citation attaches to a *value*, not to a sentence. Flatten to a string in the executor
and `27551.84` can no longer be clicked for its ten cells — the same loss as `pd.read_excel`
discarding cell addresses, one layer up.

This is also the whole "PPT is not a rewrite" claim made concrete: every renderer joins template
and slots its own way. HTML makes numbers clickable, PPT puts citations in the notes field, PDF
makes them footnotes. Nothing upstream changes.

**Two kinds of citation, distinguished by `slot_sources`:**
- a **label** slot (`region`, `state`) came from one cell — the section header or the row label
- a **metric** slot is computed and exists **nowhere** in the sheet — cite every contributing cell

That distinction is what "where did this number come from?" actually needs. For a lookup the
honest answer is one cell; for an aggregate it is *"nowhere — it is the sum of these ten"*.

---

## D33 — A good plan means a plan that compiles
**LOCKED**

`CascadePlanner` now validates each tier's plan by *attempting to compile it*. A whitelist
violation or slot-contract violation becomes an escalation instead of a crash.

`PlanFailure` moved to `plan.py` (no dependencies) so both `planner.py` and `compile.py` can raise
it without a circular import.

**Found by:** Ollama emitting `"{v1} used the most diesel in {v2}, at {v3} thousand tonnes."` — it
put the *year* in a hole. Three holes, two returned values, `KeyError: 'v3'` at render time.

**Two fixes, deliberately both:**
1. *Structural* — `compile_plan()` raises when the narration's holes do not exactly match its
   slots. This is D31's contract, finally enforced rather than assumed.
2. *Prompt* — a hole is only for a value the query returns (the metric, and any group_by label);
   filter values are written out as words.

The structural check is the guarantee; the prompt rule is the improvement. Same pairing as D9.

**Same class of bug as D28:** a quality gate sitting outside the routing loop crashes instead of
escalating. Both are now inside `CascadePlanner`.

---

## D34 — Coverage must ignore labels equal to a compiler default
**LOCKED**

`row_kind` has a value literally named `state`, so *"Which **state** consumed the most petrol"*
made `coverage_gaps` demand a `row_kind` filter and abstain on a perfectly answerable question.

A collision between one column's **name** and another column's **value**.

Fix: a label match never counts when the label equals that column's compiler default —
the same rule already applied to value aliases, now applied to direct label matches too.

```python
hit = any(names(lbl.lower()) and lbl != default.get(col.name) for lbl in col.labels)
```

**Third false positive from this gate**, after `"east" in "least"` and the alias/default case.
Reinforces D27: `coverage_gaps` is a cheap routing hint, not a correctness guarantee. Every one
of its bugs caused a *wrong abstention* — refusing a question the system could answer — which is
the over-refusal failure mode from Lesson 0.5.

---

## D35 — Refusals are composed by us, from a model-chosen code
**LOCKED**

`Abstain` carries `reason_code` + `detail`, never prose. `compose_refusal()` writes the sentence
from the catalog.

**Found by:** the model refusing 2030-31 with *"the table has data only up to 2023-24"* — the file
runs to **2025-26**, and the catalog says so. The refusal was right; the stated fact was false.

D1 was enforced rigorously for answers and left open for refusals. Same fix, same principle:
**the model decides, the engine writes anything factual.**

```
no_such_column       → "there is no tax column in this file. It has: product, region, ..."
value_not_in_column  → "'year' in this file only contains: 2008-09, ... 2025-26"
not_a_data_question  → "that is not a question this table can answer"
empty_result         → "the query was valid but matched no rows"
```

---

## D36 — Gates that are facts about the question run BEFORE the planner
**LOCKED** — `src/ask.py` is the front door

Two checks moved ahead of the model:

**Absent concepts.** The catalog declares what it does *not* contain (`tax`, `revenue`, `price`,
`population`, …). A question naming one abstains mechanically, with **zero API calls**.
The mirror of D17: D17 says which concepts exist, this says which are known-missing, and both
turn answerability into a lookup.

Forced by a regression: adding reason codes made abstaining harder for `llama3.2:3b`, which
began answering the tax question instead of refusing it. Coverage could not catch it — every
column it named was real. A set lookup cannot get this wrong; a 3B model demonstrably does.

**Ambiguity.** `find_ambiguity` originally inspected the *plan*, so it never ran: the planner
either resolved "Uttar" invisibly or abstained. Ambiguity is a property of the **question against
the catalog**, so it is checked before planning. `"uttar"` → UTTAR PRADESH / UTTARAKHAND → ask
(D20).

**The general lesson:** a check belongs at the earliest point where the information it needs
exists. Both of these were placed after the model, where the evidence had already been destroyed.

---

## D37 — `min`/`max` as the metric while grouping and ranking is rejected
**LOCKED**

```python
if plan.metric.agg in ("min", "max") and plan.group_by and plan.limit:
    raise PlanFailure(...)
```

**Found by domain knowledge, not by the system.** *"Which region used the least diesel in
2020-21?"* answered **SOUTH, 15.98** — implausible for a region containing Tamil Nadu and
Karnataka. `15.98` is Lakshadweep's figure: `min(value) GROUP BY region ORDER BY 2 ASC` finds the
region containing the smallest single row, not the smallest total. Correct answer:
**NORTH EAST, 1,775.69**.

It passed **every** gate — coverage saw all filters, the compiler saw valid columns, and
`check_lineage` passed because for `min` the rule is "the answer is one of the cited values", and
15.98 genuinely was.

This is the exact failure class named in D27: a wrong aggregation, invisible to coverage. Fixed
structurally *and* in the prompt, the D9 pairing.

---

## D38 — A scoreboard that does not check the number is false confidence
**LOCKED**

`tests/eval_e2e.py` now asserts expected slot **values**, not only status and citations.

The wrong SOUTH answer scored **9/9**. The eval checked that an answer had citations and the
right status — never that it was right. That is worse than no scoreboard, because it converts an
unknown into a false assurance.

The suite also moved from testing `planner` in isolation to testing `ask()` — the actual front
door — after two gates were added upstream of the planner and the old eval could not see them.

> Test what ships, and assert the thing you actually care about.

---

## D39 — Provider registry, and a key the UI can supply
**LOCKED**

`PROVIDERS` in `planner.py` maps a provider name to base URL, env var, model list, prices and any
provider-specific body. Every entry speaks the OpenAI chat-completions dialect, so **one class
serves them all** — adding OpenAI, Groq or Together is a dict entry, not a new class. D22's seam,
made operational.

`make_planner(provider, model, api_key, local_first)` builds the cascade explicitly; the sidebar
drives it. Hardcoded to DeepSeek today, model-agnostic by construction.

**Key handling:** an explicitly-passed key (typed in the sidebar) wins, otherwise the environment
loaded from `.env`. Sidebar input is `type="password"`, held for the session only — never written
to disk, logged, or placed in a prompt. `.env` stays gitignored.

---

## D40 — The renderer joins and draws. It never computes or formats.
**LOCKED**

`streamlit_app.py` renders `Value.formatted`; it never formats a figure itself. If it did, it
would become a second place numbers are produced and `check_digits` would correctly fire.

That narrowness is the "PPT is not a rewrite" claim made concrete — a renderer that only joins a
template with slots and draws citations can be re-pointed at `python-pptx` or `weasyprint` in
~30 lines.

**Verified in the browser, all three statuses:**
- *answered* — "NORTH used the most diesel in 2024-25, at 27,551.84 thousand tonnes", expanding to
  `HSD!A9` for the label and `HSD!R10`–`R19` for the number, plus the SQL actually run
- *abstained* — instant, **zero API calls**, refusal composed from the catalog
- *clarify* — "uttar" → two buttons → re-asked → "Uttarakhand consumed 744.01 thousand tonnes of
  diesel in 2019-20", matching `HSD!M19 = 744.0101747008262` in the source workbook

---

## D41 — Cache the Answer across Streamlit reruns
**LOCKED**

Streamlit re-runs the entire script on *every* widget interaction. Expanding a citation panel was
firing a fresh planner call — measured: spend went `$0.00000 → $0.00008` from opening a disclosure
triangle.

The `Answer` is now cached in `session_state` against `(question, provider, model, local_first,
has_key)`, so clicking around a result is free. Only a genuine change re-plans.

**Also fixed:** `st.session_state.q cannot be modified after the widget with key q is
instantiated`. The example buttons sit *above* the input and worked; the clarification buttons sit
*below* it and raised. Both now use `on_click` callbacks, which run before the rerun. And the
resolution replace is case-insensitive — the flagged term is lowercased (`"uttar"`) while the
question says `"Uttar's"`, so `str.replace` silently did nothing.

---

## D42 — Trace is opt-in, and the SQL shown is readable
**LOCKED**

**Trace behind a toggle.** By default an answer is one sentence. Citations, the plan-echo and the
SQL appear only when "Show trace" is switched on. The toggle label carries the count
(`· 11 source cells`), so the lineage claim is visible before anyone clicks and the click is the
reveal.

Reasoning: in a demo the interesting moment is *"pick any number — where did it come from?"*
Showing the evidence unprompted spends that moment before it is asked for.

**Readable SQL.** A wall of `?` is unreadable on a screen someone is reading over your shoulder,
so the displayed statement has its parameters filled in:

```sql
SELECT sum("value") AS v1 FROM consumption
WHERE "state" = 'GUJARAT' AND "product" = 'HSD' AND "year" = '2019-20' AND "row_kind" = 'state'
```

**Formatted across clause lines**, not one long line — `SELECT` / `FROM` / `WHERE` / `GROUP BY` /
`ORDER BY` / `LIMIT` each on their own line, `AND` conditions indented under `WHERE`. Deterministic
string work, no parser dependency: the statement shapes this compiler emits are known and small.

```sql
SELECT "region" AS v1, sum("value") AS v2
FROM consumption
WHERE "product" = 'HSD'
  AND "year" = '2024-25'
  AND "row_kind" = 'state'
GROUP BY "region"
ORDER BY 2 DESC
LIMIT 1
```

Worth noticing in that output: `row_kind = 'state'` appears although nobody asked for it — the
compiler injected it (D9). Making it visible turns an invisible guarantee into something a
reviewer can interrogate, and the answer is the 3.08x double-count you get without it.

**The parameterised form stays one click deeper**, under "the statement as actually executed".
That separation is load-bearing: D30 says values never enter the SQL string, and the readable
version is a display convenience, not a claim about what ran. Collapsing the two would quietly
contradict the guarantee it is meant to illustrate.

---

## D43 — Engineering artifacts go to a trace log, not the screen
**LOCKED**

The parameterised statement (`WHERE "product" = ?` with a separate params list) was showing in the
UI. It is a debugging artifact: nobody reading an answer needs it, and it makes the trace look
like machinery rather than evidence.

**What the user sees:** the cited cells, the plan in plain English, and the SQL with values filled
in — readable, formatted across clause lines.

**What goes to `logs/trace.jsonl`:** one JSON line per question, holding the raw plan, which tier
produced it, model name, the parameterised statement plus its params, every cited cell, the
rendered text, and elapsed ms. This is the file to open when an answer looks wrong.

Never logged: the API key or anything else from the environment. Only the model name.

**The log already earns its place** — the timings prove an architectural claim rather than
asserting it:

```
answered    21371ms   tiers=[Ollama, DeepSeek]   ← local tried, escalated
abstained       0ms                              ← never touched a model
```

`logs/` is gitignored — artifacts, not source.

**The distinction worth keeping:** *evidence* is user-facing (it answers "where did this come
from?"). *Diagnostics* are not (they answer "why did the system do that?"). Both matter; only one
belongs on screen.

---

## D44 — "The data lacks it" and "I can't compute that" are different refusals
**LOCKED**

*"What is Bihar's share of total consumption of petrol"* returned *"the query was valid but
matched no rows"*. The refusal was right; the reason was a lie. Two separate bugs.

**Bug 1 — a self-contradictory plan.** The model read "total" and emitted
`state = BIHAR AND row_kind = 'all_india'`. Total rows carry no state (D19), so that can never
match. Statically impossible, and now rejected in the compiler as a `PlanFailure` rather than
silently returning zero rows and blaming the data.

**Bug 2 — the question is not expressible.** A *share* is a ratio: Bihar's petrol ÷ all-India
petrol. The plan format has one metric and no division, so no plan could answer it.

**New reason code: `unsupported_operation`.** Detected mechanically before the model runs, on
`share`, `percentage`, `proportion`, `ratio`, `per capita`, `growth rate`, `cagr`.

> answering that needs a ratio between two aggregates, which this system cannot express yet — it
> computes one aggregate per question, not ratios between two. Ask for the parts separately and
> they will each be traceable

**Why the distinction matters.** "The file has no tax data" means *stop*. "I can't divide two
numbers yet" means *rephrase* — and the rephrasing works: "What was Bihar's petrol consumption in
2024-25?" answers 1,161.23, cited to `MS!R33`, verified against the source workbook.

Conflating them tells the user their question is impossible when it is merely unsupported. That is
a subtler form of the over-refusal failure from Lesson 0.5.

**The honest framing for the interview:** this is a real limit of the constrained plan format
(D3), surfaced honestly rather than papered over. D22 is where it lifts — a SQL-writing planner
handles ratios natively, and the validator does not change.

---

## D45 — Derived values: two measures and an operation
**LOCKED** — supersedes the `share` half of D44

A third plan kind, `derived`: two `Measure`s plus a `Derivation` (`divide`, `subtract`,
`percent_change`, with a `scale`). One schema change covers **share, growth rate, difference and
per-unit**, because they are all "two aggregates and an operation".

**Rejected — special-casing "share".** The next question is growth rate, then per-unit, and you
are writing a case per phrasing.
**Rejected (for now) — letting the model write SQL.** SQL does ratios natively, but validation
becomes parsing and provenance becomes hard: which rows fed a window function? That is the thing
being graded. Still the D22 path when a stronger planner is worth it.

**The arithmetic is ours.** `OPS[op](a, b) * scale` runs in `execute.py`. The model chose *what*
to divide; it never saw either number. D1 is untouched.

**`compile_measure()` reuses `_compile()`** — a measure is a plan with fewer parts, so it passes
through exactly the same whitelist, compiler defaults and contradiction checks.

---

## D46 — A derived value's lineage is a tree
**LOCKED**

`Value` gained `parts` and `derivation`. A derived number cites **nothing directly** — it exists
nowhere in the spreadsheet — and its provenance is its inputs' provenance.

```
2.90%                                        computed, cites nothing directly
├── m1 = 1,161.23   ← MS!R33                 1 cell
└── m2 = 40,004.53  ← MS!R10 … MS!R55       36 cells
```

This is a **better** answer to "where did this number come from?", not a worse one: it shows the
arithmetic as well as the cells. `Answer.all_citations()` walks the tree, so 37 cells are still
reachable as a flat list.

**And `check_lineage` gets stronger, not weaker.** For a derived value it verifies each input
against its own cells *and then* the operation between them. Confirmed by tampering: forcing the
value to 99.9 is rejected with *"derivation 'm1 / m2 x 100' on 1161.232 and 40004.527 gives
2.9027…, but the reported value is 99.9"*.

---

## D47 — `overfilter_gaps`: the mirror of coverage
**LOCKED**

`coverage_gaps` catches the model **dropping** a filter. Nothing caught it **inventing** one.

**Found by:** *"what is Bihar share of total consumption of petrol"* — a question with no year in
it — came back scoped to **2024-25**. The model added a constraint nobody asked for and silently
answered a narrower question.

`overfilter_gaps()` flags any filter whose value the question never names, by label or by alias.
Compiler defaults are exempt (D9, D24). It runs in the cascade beside coverage, so an invented
filter escalates rather than shipping — measured: Ollama's invented year escalated to DeepSeek,
which correctly omitted it.

**Also fixed:** `check_digits` crashed on a legitimate narration such as *"…in 2019-20"*, because
a narration may properly restate a **filter** value. Filter values are now allowed, since filters
are validated by their own gates. Computed numbers still cannot get in — they only arrive through
slots.

**Denominator ambiguity, structurally.** A share is only defined once you know what the total is
over. The rule: the denominator must relax the numerator by **exactly one** dimension — a state's
share of India keeps the product and the year and drops only the state. Dropping two is a second
reading, so it asks (D20). Chosen over phrase-matching because it depends on the plan's shape
rather than the wording, and therefore works on any dataset.

---

## D48 — A default value is not the same as aggregating across a dimension
**LOCKED**

*"What share of consumption does Bihar contribute to?"* answered **2.76%** — silently summing
**18 years** across 655 cells. Two things were unspecified, and only one of them was fine:

| omission | what happened | verdict |
|---|---|---|
| `product` | compiler default `ALL` | ✅ fine — `ALL` is a real value in the sheet meaning "all products" |
| `year` | **no filter at all** | ❌ aggregated across the whole dimension |

> A **default value** is a choice *within* a dimension. **Aggregating across an entire dimension**
> is a choice about *scope*. Only the second needs surfacing.

It matters: **2.76%** cumulative over 18 years vs **2.79%** for 2024-25 — different questions,
and someone asking "what share does Bihar contribute" almost certainly means now.

### Scope dimensions are inferred, with a spec override
A column is scope-defining when ≥80% of its labels match a period pattern (`2008-09`, `2024`,
`Q1-2024`, `Jan-24`, ISO dates). `ANNOTATIONS` can declare or suppress it explicitly.

Measured on this file with **zero configuration**: `year` → True, every other column → False.

**Why inference is acceptable here when D16 argues against guessing:** blast radius.

| guess | if wrong |
|---|---|
| header row (D16) | every number *and* every citation wrong, confidently |
| which dimension defines scope | we ask an unnecessary question, or miss one |

The first corrupts answers. The second only affects **whether we ask** — and asking is already
the safe behaviour. Not all guesses are equally dangerous.

**Rejected — "any unnamed dimension with many values".** Purely structural, no config, and wrong
on 2 of 3 real questions: it fires on `region` for *"Gujarat's diesel in 2019-20"*, unable to see
that `state` already pins the region. Exactly the over-asking D27 warns about.

### The escape hatch
"all years", "every year", "across all years" is itself a scope choice, so the check must not
fire again — otherwise the clarification's own answer loops. The `all years` button re-asks with
that phrase and the broad reading goes through.

Buttons offered: `all years`, then the three most recent values.

---

## D49 — Two honest-refusal fixes
**LOCKED**

**Relative time references are not invented filters.** `overfilter_gaps` (D47) demanded that a
filter's value appear literally in the question. *"...compared to previous year"* resolves to
`2024-25`, which is correct, so the check escalated a good plan and eventually abstained on an
answerable question. Scope-dimension filters are now exempt when the question carries comparison
language (`previous`, `last`, `prior`, `year-over-year`, `compared to`, `growth`, `increase`), or
when the plan is `derived` — where the two measures differ by period *by construction*.

A check added to catch invention started refusing legitimate resolution. **A new gate needs its
own false-positive test, not only its true-positive one.** Fourth false positive from this family
of checks.

**Planner exhaustion is not "not a data question".** When every tier failed the cascade abstained
with `not_a_data_question`, composing *"that is not a question this table can answer"* — blaming
the file for our planner giving up. New code `planner_failed`:

> I could not build a reliable query for that question. The data may well support it — this is a
> limit of the planner, not of the file. Try rephrasing, or naming the state, fuel and year
> explicitly.

Three distinct refusals now, and the difference matters to the user: **the data lacks it** (stop),
**the system can't compute it** (rephrase into parts), **the planner failed** (rephrase more
explicitly).

---

## D50 — A derived plan must be shaped correctly *for its operation*
**LOCKED**

*"How much % did petrol consumption in Bihar increase in 2025-26 compared to previous year?"*
answered **3.14%**, computed as Bihar's 2025-26 petrol divided by **ALL INDIA's** 2024-25 petrol,
narrated as growth. Correct answer: **8.23%**.

Two model errors at once — the denominator dropped the `state` filter, and the operation was
`divide` instead of `percent_change`. **No gate caught either.** D47's denominator check only
counted *how many* filters were dropped (one — `state`), never whether the result made sense for
the operation.

`check_derivation_shape()` enforces the shape per operation:

| op | required shape |
|---|---|
| `divide` (a share) | the denominator **relaxes** the numerator: it may drop columns, but every column they share must hold the **same value** |
| `percent_change` / `subtract` (a comparison) | **identical** filters except exactly one column, and that column must be the scope dimension |

The Bihar plan fails the first rule immediately: both measures filter `year`, with different
values. A denominator on a different year is not a total — it is a different question.

Run in the cascade, so a bad shape escalates instead of shipping. Plus a worked `percent_change`
example in the prompt — structural first, prompt second (D9).

**Verified against the source workbook:** `MS!S33` = 1256.825, `MS!R33` = 1161.232 →
**8.2321%**. Two cells, one per period — a comparison, not a share.

---

## D51 — Entity matching must tolerate typos
**LOCKED**

*"what % change does **Maharastra** shown in Petrol consumption last year"* — one missing `h` —
was refused outright. Both models resolved the typo correctly to `MAHARASHTRA`, and
`overfilter_gaps` (D47) flagged the filter as **invented** because the label did not appear
literally in the question. Both tiers escalated, then the cascade abstained.

`named()` now falls back to `SequenceMatcher` at **0.82**, comparing question words against the
label and against each word of a multi-word label (so "tamilnadu" matches `TAMIL NADU`).

Measured separation: `maharastra`/`maharashtra` **0.95**, `gujrat`/`gujarat` **0.92**,
`kerela`/`kerala` **0.83** — against `punjab`/`puducherry` **0.25**, `bihar`/`bengal` **0.36**.
Typos clear the bar; unrelated states are nowhere near it.

**Fifth false positive from this family of checks** (after `"east" in "least"`, the alias/default
case, `row_kind`'s `state` value, and relative time references). Every one refused an answerable
question.

> A gate needs its own false-positive test, not only its true-positive one. Real users misspell
> entity names constantly, and a check that cannot tell a typo from an invention is a check that
> refuses real questions.

---

## D52 — A relative period needs an anchor
**LOCKED**

*"…Petrol consumption **last year**"* silently chose 2024-25 vs 2023-24 → **7.44%**. The file runs
to 2025-26, so "last year" could equally mean 2025-26 vs 2024-25 → **6.29%**. A silent choice that
changes the answer.

The distinction that makes this detectable:

| phrasing | anchored? |
|---|---|
| "in **2025-26** compared to the previous year" | ✅ an explicit period anchors it |
| "**last year**" | ❌ relative to nothing |

`find_unanchored_period()` fires when the question uses `last`/`previous`/`prior`/`recent`/
`latest`/`this` + a period word **and** names no explicit period value. Runs before the planner —
no model call — and offers the three most recent periods as buttons.

Verified: the unanchored phrasing now asks; the anchored one answers **6.29%**, matching
`MS!S44` = 4645.513 and `MS!R44` = 4370.547 in the source workbook.

**Same family as D48.** D48 catches a scope dimension left *unconstrained*; this catches one
constrained by a reference that could point at more than one value. Both are silent choices about
scope, and both are cheap to surface.

---

## D53 — Two suites: a small end-to-end one, a large two-sided gate one
**LOCKED**

Five of the project's bugs came from gates that fired when they should have stayed quiet, and
every one **refused an answerable question**. The root cause was a testing habit, not a coding
one: each gate was tested only for what it should *catch*, never for what it should *ignore*.

### The split

| suite | speed | cost | size | proves |
|---|---|---|---|---|
| `tests/eval_e2e.py` | ~20 s/question | ~$0.001/run | **15** | the system works, with real lineage |
| `tests/eval_gates.py` | milliseconds | **free** | **63** | the gates are calibrated |

The gates are pure functions — no model, no network, no database — so they can be tested
exhaustively for nothing. The end-to-end suite stays small because it costs money; the gate suite
should be as large as the input space demands.

**Every gate carries both lists.** Currently 26 should-fire and **37 should-stay-quiet** — the
quiet cases outnumber the loud ones, which is the correction for how these bugs actually happened.

### The suite immediately found a real bug

Everything passed on the first run, which is *suspicious rather than reassuring*: the tests were
written after each fix and largely described existing behaviour. Probing for cases I had **not**
already fixed found one:

```
ANDHRA PRADESH  vs  MADHYA PRADESH   0.86     ← above the 0.82 fuzzy threshold
CHANDIGARH      vs  CHHATTISGARH     0.73
JHARKHAND       vs  UTTARAKHAND      0.70
```

D51's typo tolerance would wave through a plan filtering `MADHYA PRADESH` for a question about
**Andhra** — a genuinely wrong state, accepted as a typo.

**Fix (D53a): best match, not close enough.** `named()` now requires the filter's value to be the
label the question word matches *best* among that column's labels. If another label fits better,
the model picked the wrong one and the gate fires. Aliases stay literal-only, since they are exact
vocabulary rather than approximations.

That is the calibration both bugs needed at once — too strict refused "Maharastra"; too loose
accepted "Madhya" for "Andhra". Neither a lower nor a higher threshold fixes both; the *relative*
test does.

> A gate is not a threshold, it is a comparison. "Close enough" admits the wrong answer whenever
> two right answers are close to each other.

---

## D54 — Matching resolves three ways: none, one, many
**LOCKED** — Gaurav's design, replacing the binary best-match rule of D53a

D53a asked "is the filter the best match?" — binary. It handles a clear winner and a clear
miss, and **silently picks a winner when two labels tie**.

`resolve(word, labels) -> Resolution(kind, candidates, score)` returns:

| kind | when | example |
|---|---|---|
| `none` | nothing scores above FUZZY (0.82) | `bangalore` → best is WEST BENGAL at 0.67. It is a city, not a state in this file |
| `one` | a clear winner, runner-up more than MARGIN (0.08) behind | `maharastra` → MAHARASHTRA 0.95, next 0.59 |
| `many` | two or more within MARGIN, **or** the word is contained in several labels | `pradesh` → five states at 1.00; `uttar` → UTTAR PRADESH and UTTARAKHAND |

This is the **map / ask / abstain** three-way (D20) finally applied to matching itself. It also
unifies two mechanisms that were separate: `find_ambiguity` used substring containment,
`overfilter_gaps` used similarity. Both now speak through one resolver.

**Two kinds of ambiguity, both real:**
- *containment* — the term sits inside several labels (`uttar`, `daman`). Similarity alone calls
  this a clear winner, because `uttar` scores 1.00 against UTTAR PRADESH.
- *similarity* — the term is equally close to several (`pradesh`).

### Two regressions the change caused, and their shared fix

**`"least"` matched EAST at 0.89.** The `"east" in "least"` bug returning in fuzzy form. Fuzzy
matching exists for *typos*, which roughly preserve length; on short words it finds unrelated
English instead. Guard: `FUZZY_MIN = 5` — below that, literal matching only. `GOA` and `EAST` are
still reachable literally.

**`MADHYA PRADESH` was accepted for a question saying "Andhra Pradesh".** The word `pradesh` ties
five states, and the tie branch excused any tied candidate.

Both fixed by one rule: **a label spelled out in full settles that column.** No word-level
guessing runs when the question could not have been clearer. Ambiguity skips the column entirely;
overfilter stops excusing ties.

> Word-level analysis is a fallback for when the question was imprecise. It must never override
> the question being precise.

**Suites after the change:** gates **67/67** (27 fire, 40 quiet), end-to-end **15/15**.

---

## D55 — A share must divide by something strictly wider
**LOCKED**

`check_derivation_shape` required the two measures to *agree* on shared columns, but did not
require the denominator to actually be wider. So a plan with identical filter sets on both sides
passed — and returns **100%** by construction.

Found while testing whether an LLM could be trusted to decide which measure a follow-up edits.
Three ways to handle *"what about Maharashtra?"* after a Bihar share:

```
✓ accepted   numerator MH  /  denominator all-India      correct
✗ accepted   numerator MH  /  denominator MH             always 100%
✓ REJECTED   numerator MH  /  denominator still Bihar
```

The middle case is the one a follow-up produces naturally, by editing *both* measures.

**Rule added:** for `divide`, if the two measures filter on exactly the same columns, reject —
that ratio is 1 by construction and is never a real question.

**Why this mattered more than the bug itself:** it is what makes D56 safe. Once every way of
getting a derived follow-up wrong is mechanically caught, the model can be trusted to decide
*which* measure to edit — no hand-written rule per operation.

> I reached for a hardcoded per-operation rule because I had not noticed our own validator
> already did most of the job. Check what the guarantees already cover before adding a new one.

---

## D56 — Conversational memory: provenance, not lexical grounding
**LOCKED** — window 2 turns, session-scoped

Our gate asked *"is this filter a word in the question?"*. A follow-up inherits filters the
question never contains, so that check refuses every follow-up. It now asks **"does this filter
have an authorised source?"** — three are legal:

| provenance | verified by |
|---|---|
| `stated` | the original lexical check, unchanged |
| `inherited` | must match a recent turn's plan exactly |
| `default` | must match a registered compiler default (D9, D24) |

Anything else is still an invention and still fails. **The gate does not get looser** — it gains a
second, equally mechanical check. `HSD` in turn 2 is not invented; it is inherited *with a receipt*.

### Precedence: stated beats inherited
If this turn names a value, it wins; inheritance only fills gaps. So *"which region used the most
petrol?"* after a diesel question cannot silently keep diesel. Not a prompt instruction — the
order the branches run in.

### Why carry the plan, not the chat
The literature says the two tie on accuracy (Liu et al.: *"little difference … using precedent SQL
as context gives almost the same effect with using recent questions"*). They do **not** tie for us:

- raw chat history → *"the model saw the last two questions and decided"*. No artifact, nothing to
  verify, nothing to render, nothing to delete.
- the previous plan → `product = HSD, inherited from turn 1`. A checkable fact.

Our whole system is "every value has a source you can point at". Only one option keeps that.

**Rejected — rewrite the follow-up into a standalone question.** My first instinct, and the one
clearly wrong choice. CoE-SQL measured rewriting at 38.9% vs 46.7% for *doing nothing* and 50.5%
for editing the plan; QURG measured hard replacement at 52.4 vs 64.9 for keeping history.

And it is uniquely dangerous **here**: if the rewriter drops "2019-20", the plan has no year, the
question string has no year, and our gate — which compares plan to question — passes. We would
have laundered an omission into a valid plan, checking the model's output against the model's
other output.

**Window: 2 turns.** An ablation found a 2-turn window took accuracy from 0% (stateless, by turn
3) to 74–86%, while richer memory swung between +14 and −16 points. Short is the finding, not a
compromise.

**Session-scoped, with a clear-chat button.** Nobody can detect topic change automatically —
Databricks, Microsoft and Metabase all ship a reset button *and* documentation telling users to
press it. Three vendors independently admitting the same limit.

**Inheritance is visible**, marked in the plan-echo (`year = 2019-20 (inherited)`). Silent
inheritance is the failure mode; showing it is the fix.

**Almost entirely generic** — provenance tags, precedence, the window and plan-diffing carry no
dataset knowledge. That falls out of D22: because we compile a structured plan rather than raw
SQL, the artifact that carries context already exists. Everyone else reconstructs it from SQL
strings.

**Honest weaknesses:** (1) topic change is undetectable — hence the button; (2) cross-session
memory is out of scope, since persisting it produces "confidently wrong queries keyed to the wrong
entity"; (3) an inherited *period* is allowed through silently rather than triggering the scope
gate, because asking "which year?" on every follow-up would defeat the feature — it is shown in
the echo instead. That is a deliberate trade, not an oversight.

---

## D57 — One definition of "stated", and a cache key that cannot invalidate itself
**LOCKED** — two bugs found while wiring D56, both worth keeping

**Two definitions of "stated" drifted apart immediately.** `named_in` checked catalog *labels*;
`overfilter_gaps` also checked *value aliases*. So `product = HSD` from the word "diesel" was
`stated` to one and `invented` to the other — and the memory tests failed on turn 1, before any
follow-up was involved.

The `named_in` docstring literally said *"two definitions of 'stated' would drift apart"* — and I
then created a second one by leaving aliases out. Fixed by moving the alias check **inside**
`named_in`, so there is exactly one implementation both callers use.

> A comment warning about a hazard is not a defence against it. Only a single implementation is.

**The Streamlit cache key invalidated itself.** The key included the conversation's questions, so
answering a question *changed the key that had just been set for it*: cache miss on the rerun,
`ask()` ran twice, the same question appeared twice in the sidebar, and one click cost two API
calls (measured: `$0.00000 -> $0.00012`).

Fixed with a `Memory.epoch` counter bumped **only** on `clear()`. The key must change when the
conversation is *reset*, and must not change merely because a turn was *recorded*.

> A cache key must not depend on state the cached operation mutates.

**And a third, same family as the log-ordering bug (D-log):** Streamlit draws the sidebar before
the main body runs, so the conversation panel showed the state from *before* the turn. Fixed with
a single `st.rerun()` after a new answer is cached — free, since the answer is already cached and
cannot re-plan.

**Verified in the browser, three turns:**
```
Gujarat diesel 2019-20        -> 5,607.58   all stated
What about Maharashtra?       -> 9,528.95   product + year (inherited)
And petrol?                   -> 3,462.01   product STATED, overriding inherited diesel
```
All three match the source workbook (`HSD!M42`, `HSD!M44`, `MS!M44`).

---

## D58 — Phase 1: onboard a workbook nobody has seen
**LOCKED** — `probe.py`, `propose.py`, `spec.py`, `ingest_spec.py`, `specs/ppac.yaml`

D16 said ingest's constants must move into a per-file spec, proposed and confirmed once. Built.

### The split that makes it safe

| | who | if it is wrong |
|---|---|---|
| **geometry** — header row, label column, value columns, total rows | **heuristics**, no model | every number *and* every citation wrong, confidently |
| **meaning** — what the entity is, what to call the period, aliases, units | **model**, structure only | the assistant understands questions worse |
| confirmation | **human**, once | — |

Only the first is dangerous, so only the first is model-free. `probe.py` reports structure and
interprets nothing: per row, how many cells are filled, whether they hold text or numbers,
whether the row is merged. **No values** — the same rule the catalog follows (D17).

### Measured, on a workbook the code had never seen

`specs/ppac.yaml` was **derived, not written**, and the generic ingest reproduces the hand-written
one exactly: **2,210 rows · 12,882 receipts · 18 notes · 352 formulas · 36 states**, identical
values and identical cell addresses. Geometry matched a human reading of the sheet on every field.

The acceptance test was the **Union Budget at a Glance** — bilingual, data starting at column D,
`..` as missing markers, five sheets. Heuristics placed the header, the label columns and the
value block correctly on all five.

### Four bugs the unseen file exposed, each a generalisation the first version lacked

1. **Section headers fell outside the data block.** PPAC's first section (row 9) was excluded
   while the other four sat inside — the same kind of row classified two ways depending on
   position. Fixed by walking back over label-only rows, *guarded* so it stops at the header:
   a header also has no numbers, but it has text in the **value** columns, which is exactly what
   separates the two.
2. **The label is not always the leftmost text cell.** The Budget keeps Hindi in column D and
   English in E. Reading leftmost meant every label was Hindi and no total matched. Label columns
   come in **runs**, and the one nearest the numbers is the most specific — not an English
   preference, it also holds for numbering-then-name and category-then-item.
3. **"Mostly numeric row" is the wrong test for finding data.** Two label columns plus `..`
   markers inside numeric columns defeat it. The right test is *"at least half the VALUE columns
   hold numbers"*.
4. **The model's `absent_concepts` were exactly backwards on the harder file** — it proposed
   `['revenue', 'expenditure', 'deficit']` for the Union Budget, whose three main subjects those
   are. `absent_concepts` causes a **hard refusal before any model runs**, so a wrong entry
   silently kills real questions.

   Fixed mechanically: **a word that appears in the sheet's own labels is not absent.** The same
   word now gets opposite verdicts decided by the data — `revenue` is kept for PPAC and dropped
   for the Budget.

That last one is the pattern this whole project keeps returning to: the model may propose, but
any claim that can be checked against the data gets checked.

### What a human still supplies
Sheet **constants** — that `PT_Cons_Statewise MS` means `product = MS`. That knowledge is in the
sheet *names*, not in any cell, and nothing in the structure reveals it. Two lines on a
confirmation screen.

### Still open
The confirmation UI is specified but not built, and the app still reads the warehouse produced by
the original `ingest.py`. Wiring upload → propose → confirm → ingest into the product is Phase 2's
storage work, not more of Phase 1.

---

## D59 — Prior art check on spreadsheet onboarding, and what we take from it

Before building the confirmation screen, we looked at how other teams solve "read a spreadsheet
nobody has seen". Four distinct camps, and they disagree about *what the machine is allowed to
decide alone*.

| Camp | Example | Approach | Reported accuracy |
|---|---|---|---|
| Vision | **TableSense** (Microsoft, AAAI'19) | Treat the cell grid as a pixel matrix, run a CNN to find table boundaries | 91.3% recall, **86.5% precision** |
| Learned rules | **Pytheas** (VLDB'20) | Machine-learned rules over row patterns to locate data rows in CSV | — |
| LLM-reads-sheet | **SpreadsheetLLM / SheetCompressor** (Microsoft, 2024) | Compress the sheet to *structural anchors*, then let the model detect tables | +25.6% over raw encoding, **96% fewer tokens** |
| Product | **Flatfile**, Trifacta | Auto-map, then **always** show a human-in-the-loop review screen | n/a — the review is the product |

### The number that settles it
TableSense is a dedicated, trained, published structure detector and it is still wrong about
**1 table in 8**. Nothing we could build would beat it. And a wrong table boundary here is not a
degraded answer — it makes every number *and* every citation wrong, confidently, which is the
exact failure mode this whole system exists to prevent.

So: **no autonomous structure detection, at any accuracy.** This confirms D58's geometry/meaning
split from the other direction — not "our heuristics are good enough" but "even the best published
model is not good enough to go unconfirmed". Flatfile's business is that same conclusion: the
review screen is not a fallback for when the mapper fails, it is the deliverable.

### What we adopt
1. **Confirmation screen is now required, not optional.** Previously "specified but not built".
   It shows the grid sketch, the proposed geometry, and per-field confidence, with anything below
   `high` highlighted. Same shape as `ask()`'s three outcomes: accept · correct · reject.
2. **Anchor-based selection instead of truncation.** `propose_meaning()` currently sends
   `grid(rows=18, cols=12)` — the *first* 18 rows. SpreadsheetLLM's insight is to keep rows that
   are structurally *heterogeneous* (boundaries, shape changes) wherever they sit, and prune the
   repetitive middle. Truncation is strictly worse: PPAC's most important note is at **A57**, and
   subtotals live at the bottom of every sheet. Cheap change, strictly more signal per token.

### What we reject
- **A CNN over the cell grid.** No labelled training data, and the precision ceiling above does
  not remove the review step — so it buys nothing we need and costs a model to train and serve.
- **Letting the model choose geometry even with anchors.** SpreadsheetLLM optimises *detection*;
  we have already decided detection must be confirmed. Anchors improve what the model sees when
  naming **meaning**, which is the half where being wrong is merely annoying.

The convergence worth noting in the interview: `probe.grid()` — shape characters, no values —
was written before we read any of this, and is the same idea as structural-anchor compression.
Both arrive there for the same reason: **structure is a different signal from content, and mixing
them costs tokens and adds noise.**

---

## D60 — Anchor selection replaces first-N-rows truncation  *(implements D59.2)*

`propose_meaning()` sent the model `grid(rows=18, cols=12)` for the first **3** sheets. Both caps
were arbitrary, and both cut exactly the wrong thing.

**What replaced it.** `SheetProfile.anchors()` keeps a row on either of two independent grounds:

| ground | catches | blind to |
|---|---|---|
| its **shape** differs from the row above | header, data start/end, section breaks, data gaps | subtotals |
| its **label** is unusual (merged / total-like / section-like) | subtotals, titles | — |

Both are needed, and the second is the one that would have been missed. `Region Total` at row 20
has shape `T#########` — **identical** to the ten data rows it sums. Change-detection alone drops
every subtotal in the file, silently. Same lesson as D58's `absent_concepts`: the dangerous failure
is the one that looks like success.

**Two calibrations, found by measuring rather than reasoning.** First cut kept 43 of 59 rows:

1. **`f` and `#` are the same structural fact.** A formula cell and a typed number both mean "a
   number lives here". Treating `=SUM(...)` as a boundary made one stray formula cost three rows.
   Normalised before comparison — which is precisely why `Region Total` now depends on the label
   rule, exactly as the table above says.
2. **Keeping `i-1, i, i+1` triples every boundary.** The row *after* a change carries nothing the
   changed row didn't. Keeping `i-1, i` retains what locates `last_data_row`.

59 rows → 31 kept. The dropped runs collapse to `… 7 more rows, same shape`, so the count survives
even though the rows don't.

**The real win is coverage, not size.** These sheets are 33–59 rows, far too small for compression
to impress — the new sketch is ~2× the characters of the old one. What changed is *which* rows:

```
old (rows 1-18):  half of REGION - NORTH, nothing else
new:              every region header, every Region Total, ALL INDIA TOTAL,
                  and rows 56-57 — "Source: Oil Companies" and
                  "*Total Fig. includes IMPORTS & SEZ STATE"
```

Row 57 is the cell that explains the 18,132 gap between the region totals and ALL INDIA TOTAL. The
model had never seen it. And since cost now tracks how *irregular* a sheet is rather than how long,
the `[:3]` sheet cap went too — the Budget's sheets 4 and 5 were previously invisible.

**`grid()` is kept, unchanged**, for the confirmation screen. A reviewer checking our geometry needs
to see the rows we *didn't* keep; the model does not.

**Verified:** 67/67 gates, 8/8 memory, spec-driven ingest still byte-identical (2210 rows · 12882
receipts · 18 notes · 352 formulas). A live proposal on the Budget now names all five sheets and
gets `unit: ₹ crores`. Its one `absent_concepts` entry — `percentage of GDP` — was checked against
every label in the file rather than assumed: the string does not occur, so the claim stands.
`eval_e2e.py` was not re-run; nothing on the `ask()` path imports `probe` or `propose`.

---

## D61 — The confirmation screen  *(implements D59.1)*

`src/render/confirm_app.py`, 203 lines. Pick a workbook → propose → review → save
`specs/<name>.yaml` → ingest. Wiring it into the Q&A app stays Phase 2.

**Layout follows from what the reviewer is being asked.** Left is the sheet as it actually is —
`grid()`, all 40 rows, including the ones anchor selection dropped, which is why `grid()` outlived
D60. Right is what we concluded from it. The job is to look at one and check the other. No figure
is displayed anywhere: a screen showing numbers invites checking the *data*, which is not the
question being asked.

**Attention is directed by confidence, not by layout.** Non-`high` fields are listed once at the
top, tagged inline (`⚠️ check` / `🚩 likely wrong`), and their sheet's panel opens by default. The
first version opened panels when the file had ≤3 sheets — which puts attention wherever the file
happens to be small.

**The screen exists for two independent reasons, and the second only became obvious while
building.** D59 gave the first: no structure detector is accurate enough to go unattended. The
second is that some facts are not in the file *at all*:

| field | why no amount of reading finds it |
|---|---|
| `constants` | that `PT_Cons_Statewise MS` is petrol lives in the sheet's **name** |
| `section_dimension` | the heuristics see `REGION - NORTH` is a heading; only a person supplies the word *"region"* |
| `entity` / `period` / `measure` | what the rows and columns **are** |

**A missing input field cost 2,156 citations.** Running the accept path end-to-end returned
`2210 rows · 10726 receipts` against a known-good `12882`. The gap is exact: 2210 − 54 all-India
rows = 2156, one lost citation per regional row. Cause: `section_dimension` was proposed by the
model and had **no box on the form**, so a human looking straight at the region headers could not
supply it. Added; parity became exact.

That is the whole D59 thesis arriving from the other side. The failure was not the machine guessing
wrong — it was the machine having no way to be *told*. An unattended pipeline would have produced
2,210 correct rows with 2,156 quietly missing receipts, and every answer would still have looked
fine.

**Verified.** With no model and no hardcoded constants — geometry, plus `product=` per sheet, the
word `region`, and the names `state`/`year`:

```
2210 rows · 12882 receipts · 18 notes · 352 formulas     ← exact parity with hand-written ingest
```

67/67 gates, 8/8 memory. Also fixed while testing: changing the **File** dropdown does not
re-propose, so the screen could show one file's spec above another file's name — now it says so
instead.

---

## D62 — Show geometry, ask meaning. One product, not two.  *(supersedes D61's shape; sets Phase 2)*

Gaurav's pushback: two separate apps is wrong. The product is **one** app — upload a workbook, it
processes, it asks what it needs, and afterwards a dropdown in chat picks which workbook you are
talking about. He also named the risk himself: *"if the confirmation questions are too much, it
might be taxing for the user."*

He is right on both counts. The separate app was a phasing decision, and D61 justified it as a
design one.

**The fix for the tax is a distinction D61 missed.** The spec's fields divide by whether a human
can check them *by looking*:

| kind | example | visible in the sheet? | treatment |
|---|---|---|---|
| geometry | header row 8, data 9–55, values B–S | **yes**, it is right there | **show** — costs the reviewer nothing |
| meaning | this sheet is petrol; those headings are regions | **no**, not in the file at all | **ask** — costs attention, so spend it here |

D61 asked about everything, which is precisely the tax. Showing is free; asking is not. On the fuel
file the whole confirmation collapses to **three questions**:

1. these three sheets are variants — what differs? → `ALL / MS / HSD`
2. rows are grouped under `REGION - NORTH` — those headings are a…? → `region`
3. each row is a **state**, each column a **year** — yes? → one button

Header row, data block and value columns are never mentioned, only displayed. The full editable
form moves behind **"Something looks wrong"**.

**Low confidence promotes a field from shown to asked.** That is the same map / ask / abstain rule
`ask()` already uses — state it when sure, ask about the one specific thing in doubt — applied one
layer down. The rule was not generalised deliberately; it turned up again on its own, which is
some evidence it is the right rule.

**The dropdown is not cosmetic.** Each workbook has its own catalog, its own warehouse table and
its own `absent_concepts`. Choosing a workbook changes what the model can see, what SQL compiles
against, and what gets refused. It needs a spec store and a per-file catalog cache — and switching
workbook must **clear conversation memory**, since inheriting "for Bihar" across a file change is
meaningless.

### Phase 2, in order
1. Reshape the screen to show-geometry / ask-meaning  ← **doing now**
2. Spec store + workbook dropdown in chat (+ clear memory on switch)
3. Upload button, and merge the screen into the Q&A app

Order matters: reshaping first means what gets merged is already the version that does not tax the
user. Nothing about D59 changes — confirmation is still mandatory. What changes is that
confirmation is three questions and a preview, not a form with twenty boxes.

---

## D63 — FastAPI + React  *(overrides the recommendation in this session)*

Recommendation was to stay on Streamlit for the assignment: none of the five graded criteria is UI,
and a rewrite costs 1–2 days. Gaurav chose FastAPI + React anyway. Recorded as his call, and the
work proceeds in full.

**Stack:** Vite + React + TypeScript, Tailwind. No SSR or routing needs, and FastAPI can serve the
built bundle, so production is one process. Streamlit stays until React reaches parity — there must
always be a runnable demo.

**Why this is not a rewrite.** `Answer`, `Value`, `Citation` and `Spec` are already structured
objects, and the renderer's contract has always been *join and draw, never compute*. FastAPI
returns them as JSON; React becomes a second client of the same objects — the same argument that
makes the PPT path a renderer rather than a rewrite. The Python side gained one file.

**The contract that must not slip.** `text` is composed **server-side** and shipped whole. Shipping
`narration` ("{v1} thousand tonnes") plus `slots` and letting React join them would make the browser
a second place numbers are made — which is precisely the property that makes a hallucinated figure
*unrepresentable* rather than merely unlikely. The client receives finished sentences and evidence.

**Memory stays server-side**, keyed by `session:workbook`. A `Turn` holds a `Plan`; round-tripping
plans through a browser would put the "stated beats inherited" precedence on the wrong side of the
wire. Keying by workbook also implements D62's rule that switching workbook clears context.

**Endpoints:** `/api/providers` · `/api/workbooks[/{id}]` · `/api/files` · `/api/upload` ·
`/api/propose` · `/api/confirm` · `/api/ask` · `/api/session/{s}/clear`.

**Also landed:** `catalog.build()` takes per-workbook `annotations` and `absent` (defaults
unchanged, so every existing caller and all 67 gates are untouched), and `pretty_sql` moved to
`src/present.py` so two renderers cannot drift — the D57 failure with a longer feedback loop.

**Verified:** `GET /api/workbooks` lists `ppac`; `POST /api/ask` returns
*"Gujarat consumed 5,607.58 thousand tonnes of diesel in 2019-20"* citing `PT_Cons_Statewise HSD!M42`
with raw `5607.584527746732` — the known-good value and the known-good cell. 67/67 gates, 8/8 memory.
One real bug found on the way; see BUGS.md.

---

## D64 — The React client, and what a second workbook exposed

Built: `frontend/` (Vite + React + TS + Tailwind). `api.ts` typed client · `Chat.tsx` transcript +
three-outcome rendering · `Trace.tsx` recursive evidence panel · `Confirm.tsx` onboarding ·
`App.tsx` shell with workbook and model selectors. Streamlit stays until parity is beyond doubt.

**Three outcomes get three treatments** — answered (green, evidence beside it), clarify (amber, with
buttons, because the system asked *you* something), abstained (blue, a composed sentence). Flattening
them into one "response" style is how a deliberate refusal starts reading like a failure.

**`Trace` renders recursively**, because a derived value is a tree: a ratio carries its numerator and
denominator, each with their own cells. Verified through the API — Bihar's petrol share returns
`2.90%` with 37 cells split `m1` = 1,161.23 (1 cell) and `m2` = 40,004.53 (36 cells).

**Bugs found and fixed while wiring it up**

1. **A provider with no key still built the paid tier**, so "run the free local model" returned 400.
   The provider is now dropped when no key was supplied — mirroring what Streamlit already did.
2. **Transcript died on tab switch.** `turns` lived in `Chat`, which unmounts when the onboarding tab
   opens; component state dies with the component. Lifted to `App`. Switching *workbook* still clears
   it, deliberately — server memory is keyed `session:workbook`, so a transcript from another file
   would sit above an empty memory.
3. **PPAC's absent-concepts leaked onto the Budget** via `or None`. See BUGS.md — this is D58's
   failure arriving through a fallback instead of through a model.

**Verified end to end in the browser:** pick a workbook → read → confirm → ingest → it appears in the
dropdown → askable. Both `ppac` and `budget_at_a_glance` are now live workbooks, which is the first
time the system has held more than one — and that alone found bug 3.

67/67 gates · 8/8 memory · **15/15 e2e at $0.00061** · `tsc --noEmit` clean.

**Not yet verified visually:** the derived-answer tree in React (confirmed through the API only —
rendering it needs a paid key in the browser, and the local model does not plan ratios reliably).

---

## D65 — Nothing is inherited from one workbook to another

Gaurav, twice: *"You should always make it generic. Hard coding from one example of Excel sheet
does not work… Nothing should be inherited from one file or another file."* Correct both times.
This entry is the sweep.

### What moved, and where it went

| was | is now | why there |
|---|---|---|
| `COMPILER_DEFAULTS` literal in `plan.py` | `Catalog.defaults` = structural entity kind + `Spec.defaults` | a default is a claim about THIS file |
| `row_kind = "state"` in ingest | `row_kind = "entity"` — a generic vocabulary | a kind vocabulary must not borrow a word from the data |
| contradiction check on `state` | written on `catalog.entity` | holds for budget lines as it did for states |
| four hand-written few-shot examples | `worked_examples(catalog)`, generated | examples are the strongest signal in a prompt |
| `ANNOTATIONS` / `ABSENT_CONCEPTS` constants | `specs/ppac.yaml` | the workbook declares its own semantics |
| `row_kind`'s description in the spec | `catalog.ROW_KIND_ANNOTATION` | ingest produces it for every file, so it is structural |
| four hardcoded starter questions | `present.suggestions(catalog)` | a starter question is a claim about a schema |
| `data/warehouse.duckdb` in nine places | `workbook.load()` | a workbook is a spec plus the DB that spec produced |

**`row_kind` is now `internal`** — still described to the planner, still filterable, but excluded
from every check that asks *"did the user name this?"*. That answer is always no for a name this
pipeline invented, and a false yes is expensive.

**Retiring the generic vocabulary removed a bug class rather than fixing one.** D34 existed because
`row_kind` had a *value* called `state` while a *column* was also called `state`, so "which state
used the most" was ambiguous between them. With generic kinds the collision cannot occur.

### The UI, rebuilt to Gaurav's design
Sources rail on the left (each workbook described from its own spec — *"one state per row"* vs
*"one entity per row"*), one large conversation in the middle, and **onboarding inline in the
conversation** rather than on its own screen: upload → *"Reading the shape of every sheet…"* → the
few questions the file cannot answer → *"Reading every cell, and writing down where each one came
from…"* → added. A `+` beside the composer switches which spreadsheet is being asked.

Onboarding belongs in the conversation because that is what it is — the assistant asking what it
could not work out. On its own screen it read as configuration.

### Verified
67/67 gates · 8/8 memory · **15/15 e2e at $0.00165** · `tsc` clean · ingest parity unchanged
(2210 · 12882 · 18 · 352). In the browser: two workbooks in the rail, starter questions generated
per file, and the deliberately-unanswerable suggestion refusing instantly with *"there is no tax
column in this file. It has: product, region, row_kind, state, value, year"* — a refusal composed
from that workbook's own columns.

Eight bugs found during the sweep; see BUGS.md. The most expensive was that the test suites had
been reading a database the product does not ship.

### Still not generic, and known
The Union Budget's period labels are raw merged cells — Hindi and English with embedded newlines
(`"2025-26\nबजट\nअनुमान\nBudget \nEstimates"`). They are handled safely everywhere (JSON-escaped in
prompts, whitespace-collapsed in suggestions) but they are not *usable* labels. Cleaning them is a
confirmation-time question nobody is asked yet. Parked, and named here so it is not mistaken for
an oversight.

---

## D66 — Round-trip eval for ingest: make the mess, so the answer is known
**LOCKED** — 3/3 once D67 landed; — `tests/mess_maker.py`, `tests/eval_ingest.py` built; the classifier they score is not.

Ingest had one proof: it reproduced the hand-written PPAC ingest byte-for-byte. That compares the
code against *another thing the same author wrote* — if both share a wrong assumption (every sheet
is a cross-tab), they agree and it passes. The climbing file proved the assumption wrong, and there
was no answer key to catch it.

**The fix is an answer key the code did not write.** `mess_maker.py` starts from a tidy table small
enough to read by eye, deforms it with code, and records exactly where every value landed:
- `fold()` — tidy → cross-tab (PPAC shape). Ingest must melt it back. Answer = the original rows.
- `keep()` — tidy record table (climbing shape) written unchanged. Ingest must **not** melt it.

`eval_ingest.py` runs `propose()` (geometry only, no model, free) then `ingest()`, and checks two
things against the key: the reconstructed rows as a **set** (order must not matter), and each value's
**receipt cell**. `fold_messy` adds blank rows and a footnote — a metamorphic check that moving
numbers around changes neither the answer nor the addresses.

**Why this shape, and what was rejected**
- *Rejected: assert against a hand-written spec.* That is what byte-parity already did; it cannot
  catch a shared wrong assumption. Synthesis gives ground truth the author never touched.
- *Rejected: add a "did it melt?" flag to the spec now.* That builds the classifier's output before
  the classifier. The **row-set comparison is the verdict** — a melted record table comes back the
  wrong shape on its own.
- *Weakness, stated:* synthesis only tests messes we thought to make. A real government file will
  have one we didn't. So the 3 real workbooks (PPAC, Budget, climbing) stay beside it — synthetic
  for breadth, real for honesty.

**Measured now:** `2/3 files fully correct`. `fold_plain` and `fold_messy` green (melt + receipts
exact, even with spacer rows and a footnote). `keep_record` red — melted into 18 rows, `4 fields
'high'` on a wrong reading. That single red line is the specification for D67 (the role classifier):
green means the classifier leaves a tidy table alone and confidence tracks agreement, not existence.

Two bugs found on the first run; see BUGS.md (empty-insert crash; confidence-always-high).

---

## D67 — The layout classifier: crosstab vs record, and a record ingest path
**LOCKED** — `propose.classify_layout`, `SheetSpec.layout/id_columns/measure_columns`,
`ingest_spec._ingest_record`. Scoreboard `tests/eval_ingest.py` → **3/3**.

The bug D66 exposed: ingest had one costume (left column = entity, top row = period, rest = one
measure) and forced it on every file. A tidy record table (the climbing file: Name, Symbol, ISIN,
Stage, Breakout date, and three price metrics) was melted into `(date, metric_name, number)` — the
breakout date became the row key even though six companies share one date, so any aggregate over it
silently summed unrelated companies.

**The decision that makes it safe** is the block-2 test, made mechanical and put where the evidence
is (the header row), with confidence from AGREEMENT not existence:

| header over the value columns | verdict | confidence |
|---|---|---|
| values of one variable (`2008-09, 2009-10 …`) | **crosstab** — unpivot | high when all match |
| names of different things (`% vs pivot`, `Price vs 50-DMA`) | **record** — leave alone | high when clean + heterogeneous |
| merged / multi-line / mixed (`2025-26 Budget Estimates`, Hindi+English) | **crosstab (default), LOW** | flagged to confirm |

The third row is the guard that prevents a regression: the Union Budget's headers keep the year in a
row above and the estimate type — bilingual, multi-line — in the header row, so a naive "not
periodic → record" flip would stop it unpivoting and break a working workbook. A record verdict
therefore *requires clean headers*; anything messier stays crosstab and drops to LOW, which turns it
into a confirmation question (map/ask/abstain, one layer down) rather than a silent guess.

**What was built (form A, wide).** A record sheet ingests one output row per sheet row, columns kept
as they are, each identifier and each measure cell citing its own address in the same iteration it
is read (D6 unchanged). PPAC and Budget still melt, byte-identical (2210 · 12882 · 18 · 352).

**Rejected / parked: form B (one internal form with `measure_name` as a dimension).** The unifying
refactor the original chat argued for — 18 long rows instead of 6 wide — would make PPAC and the
climbing file share one downstream path and make "multiple measures" free. It needs the catalog,
planner prompt, `worked_examples`, `suggestions` and compiler to learn `measure_name`. Parked as its
own pass, not smuggled into ingest.

**Honest downstream gap, named not hidden.** The planner-facing helpers still assume the crosstab
triple: `suggestions()` returns `[]` for a record file (no period), and `worked_examples()` degrades.
Ingest and lineage are correct and the catalog lists the three measures as numeric — but a record
workbook is not yet fully *askable*. That is the next decision (D68), and it is form B.

**Weakness of form A itself.** Three measure columns mean "which is highest?" is ambiguous until the
user names the measure — the existing `scope_dimension` ask pattern, not yet wired for record.

**Verified:** eval_ingest 3/3 (fold_plain, fold_messy with spacer rows + footnote, keep_record —
row-set and every receipt cell). Real climbing file → 29 companies, 232 receipts, `F2 = 59`.
Classifier: PPAC crosstab·high (all sheets), climbing record·high, Budget crosstab·low (unchanged
ingest, newly flagged). 69/69 gates, 8/8 memory, PPAC parity exact.

---

## D68 — Record workbooks are askable on form A; the long-form rewrite was unnecessary
**LOCKED** — `catalog` (record-aware scope), `propose` (entity/measure name real columns),
`present.suggestions`, `planner.worked_examples`. `tests/eval_record_qa.py` → **3/3**.

D67 parked "make record workbooks askable" as form B — a long-form rewrite with `measure_name` as a
dimension, touching catalog, planner, compiler. **Building it revealed form B is not needed.** The
compiler, executor and lineage already handle a wide record table with zero changes: a measure is
just a numeric column, and "highest X" is `sum` grouped by the identifier with `sort+limit`. What
blocked askability was three small assumptions that the crosstab triple was always present, not the
storage shape.

**Three fixes, each where the information lived:**
1. **Scope over-fired on a date.** "Breakout date" (29 ISO dates) matched the period patterns, so it
   was tagged a scope dimension and the system asked *"which date did you mean?"* for a question that
   named a company. In a record layout a period-looking column is an attribute of one row, not an
   axis to aggregate across — so `catalog.build` no longer pattern-scopes columns for a record
   workbook.
2. **`entity`/`measure` named nothing.** With no model, they defaulted to `"entity"`/`"value"`, which
   are not columns — so every few-shot example taught the model a false schema. `propose` now points
   them at the header names of the first identifier and measure columns.
3. **Suggestions and worked-examples assumed a period.** Both gained a record branch: questions about
   an entity and its measures, built from the file's own columns.

**Verified against the source file** (not our DB): highest % vs pivot → Yasho 59.0 at `F2`; Morepen
vs 200-DMA → 90.9 at `H3`; BLS vs 50-DMA → 15.6 at `G4`. 69/69 gates, 8/8 memory, ingest 3/3, PPAC
unchanged (still state/year/value, scope=`year`).

**Form B rejected, not parked.** A long-form `measure_name` shape would unify PPAC and record files
downstream, but it buys nothing askability needs and would put the working crosstab path at risk for
an aesthetic gain. Wide record + three targeted fixes is the smaller, safer answer.

**Weakness still open.** "Which is highest?" with no measure named is ambiguous across the three
metrics; the planner will pick one rather than asking. Wiring the `scope_dimension` ask for
"unnamed measure" is the honest next step, small and independent.

---

## D69 — Ask which measure when a record file has several and the question names none
**LOCKED** — `verify.find_measure_gap`, wired in `execute` after the scope gap.
`tests/eval_record_qa.py` → **4/4**.

The open weakness from D68: a record workbook has several numeric columns (% vs pivot, 50-DMA,
200-DMA), and "which stock is highest?" names none — so the planner picks one silently, answering a
question the user did not ask. This is the scope-gap failure one column over, and the fix is the same
map/ask/abstain: state it when sure, ask about the one thing in doubt.

**Where it runs and why there.** Right after `find_scope_gap`, before executing — the earliest point
the chosen measure exists (it is `plan.metric.column`) alongside the question. It only fires for a
`sum/avg/min/max` over one of ≥2 measures; a count needs no measure, and a crosstab has exactly one,
so PPAC never sees it.

**"Named" is decided on distinctive tokens.** A token identifies a measure only if it belongs to
**exactly one** of them — so `pivot`, `50`, `200` name a measure while the shared `price`, `vs`,
`dma` do not. Matching is on whole tokens, so `50` does not match `500`.

**Verified:** "which stock is highest?" → clarify listing all three; "highest % vs pivot" →
answered; "Morepen vs 200-DMA" → 90.9; "BLS vs 50-DMA" → 15.6. 69/69 gates, ingest 3/3, PPAC
answers unchanged.

---

## D70 — The PPT path: a designed renderer, and agentic authoring on top
**LOCKED** — `src/render/deck.py` (designed deck), `src/render/deck_agent.py` (agentic),
`src/render/make_deck.py` (deterministic demo). Verified: `output/demo_deck.pptx`,
`output/story_deck.pptx`.

The roadmap promised PPT as "a natural next step, not a rewrite." Built, and confirmed by looking at
the real product (`app.coreworks.ai`): it generates *designed* presentations — themed layouts, big
KPI stat tiles, charts. So the deck had to be designed, not a bare text dump.

**`deck.py` — the renderer.** One `Answer` → one slide: a status chip, the question as eyebrow, a
72pt headline number, the composed sentence, and a **SOURCE card listing the exact cells**
(`HSD!M42 = 5607.58`). It never computes or formats a figure — every number is `Value.formatted`,
already made by execute (D40). Abstain and clarify slides render honestly with no number: a deck that
hid the refusals would misrepresent the system, and the refusals are half of what makes it
trustworthy.

**`deck_agent.py` — Gaurav's idea, built.** An agent plans the STORY (title, subtitle, a narrative
arc of 3–5 questions) and calls Python functions as tools: `ask()` per question, `build_deck()` to
render. The agent emits **English only, never a digit** — every number comes back cited from the
pipeline, and a question the data cannot support becomes an abstain slide, not a fiction. Runs fully
offline on local Ollama (llama3.2): it authored a 5-slide story about the climbing stocks, 3 cited
answers and 2 honest abstains, at zero API cost. The narrative falls back to the workbook's own
generated suggestions when no model is reachable.

**Why this is the right shape.** It is the project's whole thesis applied to authoring: the model
decides *what to say*, the engine supplies *every number and its source*. Storytelling is language;
arithmetic and lineage are the pipeline's.

**"Use the sheet to make a story", not just Q&A** *(Gaurav's refinement).* The agent does not ask
disconnected questions — the pipeline `survey()`s the sheet for CITED findings (entity count, the
leader on each measure, each welded to its cells), and the agent arranges those into a narrative
with a thesis and a closing. The closing is SYNTHESIZED from the findings, never invented: when one
entity tops several measures ("Yasho leads on every measure in this file"), that pattern is itself a
finding. The agent sees only facts the data already backs, so it edits and orders — it is never a
source of numbers. On the local 3B model the arranging step is unreliable, so a deterministic
fallback produces the thesis, order and closing; the model enhances them when reachable.

**Richer findings landed (D70.1).** `survey()` now mines three finding kinds, all cited: a top-N
**leaderboard** (via `compile_plan` + `provenance_sql({entity: name})`, so each row cites its cells),
rendered as a native **bar chart**; a **growth** finding (a `DerivedPlan` percent_change for the
leader across the full period span, lineage as a tree — on PPAC it correctly shows the 2025-26 value
comes from an external `VLOOKUP`); and single-value leaders for secondary measures. Verified: PPAC →
top-5 states chart + Gujarat +73% (2008-09→2025-26); climbing → top-5 stocks chart + per-metric
leaders. The chart is the first multi-row finding, which is what "charts" needed.

**Still parked:** the other two Coreworks outputs (Excel Analysis, Report) as further renderers of
the same objects; and ranking *derived* values (e.g. "which state grew fastest") — the pipeline does
one aggregate per question, so cross-entity growth ranking needs a new shape.

---

## D71 — Stacked headers combine into one unique period
**LOCKED** — `SheetSpec.header_rows`, `propose` (header-run detection), `ingest_spec` (combine).

The Union Budget's value columns carry a two-row header: the year (row 4) above the estimate type
(row 5). The single-header read kept only row 5, producing two columns both labelled "Budget
Estimates" — a silent collision that makes them indistinguishable. `propose` now walks up from the
header row while the value columns still hold text, and ingest combines the stacked cells top-to-
bottom into one label (`2025-2026 … Budget Estimates`), cited at the topmost (year) cell — the fact
the single-row read was missing. Single-header files have `header_rows` empty, so PPAC is byte-
identical (2210 · 12882 · 18 · 352).

**Chosen over the fuller split** (year × estimate-type as two separate dimensions): combining is a
minimal, low-risk change that unblocks the file and kills the collision without touching the
single-dimension model the crosstab path depends on. Split-into-dimensions is the next increment,
noted in the README. **Known limit:** one Budget sheet's header run walked up into a `(₹ crores)`
title row, so a couple of period labels carry that prefix — harmless (still unique) but untidy; a
confirmation-time fix.

---

## D72 — CSV/TSV upload via one loader; the pipeline never learns the format
**LOCKED** — `src/xlsx_io.py`, routed through `probe`, `propose`, `ingest_spec`; API + frontend accept `.csv/.tsv`.

Upload only took `.xlsx`. A CSV is now read into an in-memory openpyxl workbook, with numeric-looking
cells **coerced to numbers** — the probe decides a column is a measure by seeing real numbers, not
digit strings, so a CSV loaded as all-text would classify as having no measures. Coercion is
deliberately conservative: only a clean number becomes one; `2008-09`, an ISIN, a date string all
stay text, because guessing them into numbers would corrupt both the classifier and the citations.

After this boundary, probe / classify / ingest / lineage are **identical** for xlsx and csv — a
citation `Sheet1!B2` points at CSV row 2, column B. Verified both ways: a record CSV (climbing) →
29 rows, `F2 = 59`, byte-identical to the xlsx; a crosstab CSV → melts, `B2 = 1161.2`. PPAC xlsx
parity unchanged. The one direct `openpyxl.load_workbook` in `propose` and the two in `ingest_spec`
were routed through the loader; the legacy `ingest.py` (pre-D16, unused by this path) was left alone.

---

## D70.2 — Findings specific to the sheet, a trend-led story, references as a footnote
**LOCKED** — `deck_agent.survey` (trend/leaderboard/share/growth), `deck.py` (line chart, footnote).

Gaurav: the findings must be *specific to the sheet* and the story should *show trends*, with the
cell references demoted to a footnote rather than a panel.

**Trend leads.** `trend()` totals the measure per period across the whole span (each period's total
cited via the compiler's provenance query) and renders a **line chart** with the direction of travel
as the headline — *"Total value has risen 80% over 2008-09–2025-26."* That is the sheet's actual
story, not a generic leader.

**A concentration insight.** `share()` gives the top entity as a percent of the whole — *"Gujarat
alone is 12.19% of the total"* — a specific, non-obvious fact, computed as a cited `divide`.

**References are a footnote.** The big SOURCE card is gone; every slide carries a thin
`Source: …B10, B11 · =VLOOKUP(…)` line at its foot. The number and the story are the slide; the
cells are the receipt underneath, there to check but not shouting.

Order: trend → leaderboard (bar) → share → growth → secondary leaders. Verified on both files.
**Limit named:** the local 3B model still can't rank *derived* values, so "which grew fastest"
is not yet a finding.

---

## D73 — Presentation generation wired into the app
**LOCKED** — `POST /api/deck` (api.py), `api.deck()` + header button (frontend). Verified in-browser.

The deck was a script; now it is a button. A "↓ Presentation" button in the React header POSTs the
current workbook to `/api/deck`, which runs `story_deck` (mine cited findings → agent arranges →
render) and returns the `.pptx`; the client downloads the blob. Verified live: the button shows
"Building…", the request returns 200, and TestClient confirms a valid 5-slide deck.

**Two things the wiring forced, both principled.**
1. **The deck endpoint needs no planner.** `/api/ask` 400s without a key because a question needs a
   model; a deck does not — findings are mined deterministically and cited, and the narrative
   arrangement falls back to the workbook's own order. So `planner_from` is best-effort here
   (try/except → None), not required. A missing key must not block a feature that doesn't need one.
2. **Same server-side contract as ask.** The browser receives a finished file, never a raw figure —
   the number-making stays on the server, exactly as `text` is composed server-side for `/api/ask`.

**Enhancement noted:** the narrative-arrangement step (`story()`) still calls Ollama directly rather
than the caller's chosen model, so a DeepSeek key does not yet improve the prose. Passing the
selected planner into `story()` is a small, independent change.

---

## D74 — AI-native UI redesign
**LOCKED** — `frontend/src/index.css` (design system), `App.tsx`, `Sources.tsx`, `Chat.tsx`.
Logic unchanged; only styling/markup.

Gaurav: beautify the UI, make it AI-native. A cohesive visual system replaces the flat default:

- **Foundation** (`index.css`): Inter + JetBrains Mono, a warm layered gradient ground, an
  orange→rose accent, glass/card/lift utilities, fade-up and pop-in motion, animated thinking dots,
  custom scrollbars, an accent focus ring.
- **Header**: glassy sticky bar, gradient app mark, tucked model controls, gradient "Presentation".
- **Sources**: elevated cards with an accent icon and dot on the active workbook.
- **Conversation**: a gradient hero with the file name in gradient text; suggestion **cards** that
  lift on hover; user turns as gradient bubbles; answer cards with a **status accent bar** and chip
  (the three outcomes stay visually distinct — the point of D64); a bounded evidence card beside;
  an animated "planning the query…" state.
- **Composer**: one elevated rounded panel — workbook `+`, a borderless input, a gradient send
  button — with a grounding caption beneath.

Verified live in the browser (both the hero and an abstained answer render as designed); `tsc` clean.
The three-outcome distinction and the "numbers only from the server" contract are untouched.

---

## D75 — Click a cited cell to open the sheet and verify the source
**LOCKED** — `GET /api/sheet` (api.py), `SheetViewer.tsx`, wired into `Trace` (answers) and `Onboard`.

Gaurav: when a user clicks a cell reference — in an answer's evidence or during onboarding — the
sheet should open so they can verify the source. Native Excel cannot open in the browser, so the
AI-native version is an **in-app sheet viewer**: the cited address becomes a link, and clicking it
renders the actual sheet grid (column letters, row numbers, values, formulas) with that cell
highlighted and scrolled to the centre.

**One endpoint, two callers.** `GET /api/sheet?sheet=…` takes either a confirmed `workbook` (answer
citations) or a raw uploaded `file` (onboarding, before ingest) — the second matters because a file
being confirmed is not yet a workbook. Values and formulas only, bounded to ≤2000 rows / 60 cols;
the filename is reduced to its basename so a path cannot escape the data directory.

**Verified in the browser, both paths.** An answer's `PT_Cons_Statewise HSD!M42` link opened the HSD
sheet with row 42 (GUJARAT) highlighted and `5607.58` in the header; the onboarding "open the sheet ↗"
link opened the raw Climbing sheet with `F2 = 59` highlighted and every column visible. `tsc` clean.

This closes the lineage loop end to end: a number on screen → its address → the actual cell in the
actual file, in two clicks, with nothing recomputed.

**Noticed while testing (parked):** for a record workbook the onboarding line reads "names in column
E", but E is the last identifier (Breakout date), not the entity (Name, column A). A display quirk
in the geometry summary — the viewer itself highlights the right cell. Worth a follow-up.

---

## D76 — Delete a source, and a hardened "always opens" for cell references
**LOCKED** — `DELETE /api/workbooks/{workbook}`, `api.remove`, inline confirm in `Sources.tsx`.

**Delete.** A trash button on each source removes it entirely — its confirmed spec, the database it
produced, and the uploaded file — then clears the catalog cache and this workbook's conversation
memory. Deleting is destructive and irreversible, so it confirms first; the confirm is **inline on
the card** (a red Delete / Cancel), not a native `window.confirm`, because the browser dialog blocks
scripting (and an in-app confirm is the AI-native choice). Verified live: deleting
`startup_jobs_final.csv` removed its `.yaml`, `.duckdb` and `.csv`, and the card vanished; the other
three sources kept working.

**"Always opens" made a guarantee, not a hope.** The one way a reference could fail to open is a
mismatch between the sheet name in a citation and what `/api/sheet` expects. Tested exhaustively:
every distinct sheet name in every workbook's `cell_map` — 9 sheets including spaces and multi-word
names (`PT_Cons_Statewise HSD`, `Expenditure of GOI`) — resolves and returns cells. Because ingest
only ever cites a non-empty cell, the target `a1` always exists in the returned grid, so the
highlight always lands. Two live paths already confirmed (an answer's `HSD!M42`, onboarding's `F2`);
a missing file degrades to a clear in-modal message rather than a dead click.

---

## D77 — Ingestion robustness across different documents, proven by a corpus
**LOCKED** — `tests/eval_corpus.py`; fixes in `propose.classify_layout` and `propose.value_columns`.

"Does it work on any file" was a claim; now it is a scoreboard. `eval_corpus.py` builds a diverse
set of documents by deforming known-clean tables — record (1/3/5 measures), cross-tab (few/many/
quarterly/single-entity periods), blank rows + footnotes, and CSV of both layouts — and each must
round-trip: rows reconstructed as a SET equal the originals, every value cited at its exact cell.
**8/8 pass.**

Two real bugs it surfaced, each a shape the two hand-picked files never exercised:
1. **A single-measure record** (a two-column list, `City | Population`) was misclassified as a
   cross-tab, because the record rule demanded ≥2 value columns. A list with one non-periodic value
   column is still a record — now classified so, at medium confidence.
2. **A single-row time series** (one metric across many year columns) found no table, because
   `value_columns` required ≥3 values per column and a one-row sheet has one. Added a guarded
   fallback: a *contiguous* run of ≥3 numeric columns counts as a value block (contiguity stops a
   stray number in a title row from qualifying).

No regression: PPAC byte-identical, gates 69/69, ingest 3/3, record-QA 4/4. Known boundary that stays
unsupported (and fails loudly, never silently): multiple tables on one sheet, and fully transposed
sheets.

---

## D78 — Brief-driven decks: a prompt writes the questions, the pipeline keeps them traceable
**LOCKED** — `deck_agent.story_deck` (brief-aware), brief popover in `App.tsx`.

Coreworks' shape, confirmed on their use-cases page, is *data in → a brief shapes it → a traceable
narrative out* ("100% traceable" is their headline — and ours). Wired to match: the Presentation
button opens a brief box. A brief turns the agent into a **question-writer** — it proposes questions
shaped by the brief, each answered through the cited pipeline (never a raw number), and the answered
ones become slides beside the mined findings (trend, top-N, share, growth). No brief → the generic
data story. Either way every figure traces to a cell, and an unanswerable question abstains rather
than inventing.

**The trigger is a composer TOGGLE, not a separate button** (Gaurav's call). One input, two modes:
Ask (question → cited answer) or Presentation (a brief → a traceable deck). Flipping the toggle
recolours the composer and the send button and swaps the placeholder; an empty brief still builds a
generic overview. This keeps deck-making inside the conversation rather than bolted onto the header,
and it reads as one continuous tool. Verified live: toggle → generate → `200 OK` → "Presentation
ready" notice.

Verified: the deck builds with a brief; the composer toggle posts to `/api/deck`. The
question-writing step needs a capable planner to shine (the local 3B model is weak), so with no key
it falls back to the mined findings — the deck is always produced.

---

## D79 — Deep questions: typed DAG grammar (A) vs LLM-writes-SQL (B). Ran both. **PARKED** (exploration)
Prototypes in `prototypes/approach_a.py`, `approach_b.py`; two research briefs. Not wired into the app.

The limit: today's plan is one aggregate or one two-measure derivation. "Average diesel growth across
all 36 states" needs compute-per-group-then-aggregate. Two ways to get there; built both and ran them
on that exact question with DeepSeek.

**The result that reframes everything: both produced the IDENTICAL correct number — 71.6548%.** So the
choice is NOT about whether you get the right answer. It is entirely about **traceability and safety**
— which is this product's whole reason to exist.

| dimension | A — typed DAG grammar | B — LLM writes SQL |
|---|---|---|
| the number | 71.65 ✓ | 71.65 ✓ (identical) |
| **lineage** | **precise tree, free** — 72 cells, exactly 2 per state, from the typed structure | **automatic extraction FAILED** (0 rows: `year IN(...)` → contradictory `=` filters). Per-cell lineage from arbitrary SQL is "not practical today" (research) |
| safety | by construction — no free SQL, whitelist, grain-checked | an AST gate (sqlglot) + read-only latch; I hit a false-reject (aliases) — it's an arms race (`sniff_csv` holes etc.) |
| expressiveness | only what the grammar has (I added ONE node) | anything SQL can express — handled a CASE-WHEN pivot with zero grammar work |
| engineering cost | wrote a compiler; **2 bugs** (dropped filter, over-citation) | wrote a validator; **1 bug** (alias reject) + lineage unsolved |
| the failure mode | "grammar can't express the ask" → fails loud | "right number, untraceable" → fails **quiet** — the dangerous one |

**What the research adds (both briefs converge):**
- A's lineage-as-a-tree is provably correct — **semiring provenance** (Green et al.): ⊕ at aggregate/union, × at join, computed bottom-up. dbt MetricFlow / Malloy already model "aggregate of an aggregate" with a **grain type** on every node and refuse silent cross-grain — the exact safety we need. QDMR gives a proven ~13-operator inventory.
- B's blunt verdict: **per-cell lineage from arbitrary LLM SQL is not practical today** — aggregation/DISTINCT/windows collapse the mapping, no DuckDB-native provenance. The safety AST-gate is standard and solvable; the lineage is not. And correctness signals are weak (self-consistency ~1-in-8 wrong; AUROC ~0.65).

**Recommendation — hybrid, and it is what both briefs independently recommend.** Make **A the spine**:
grow the typed plan into a small grain-typed DAG (QDMR operators, semiring lineage) — it covers most
deep questions with *exact* lineage and structural safety, which is non-negotiable here. Keep **B as a
lineage-degraded escape hatch**: when a question is out of grammar, generate SQL behind the AST gate,
but present the answer as **row-level "traceable to these rows, not these cells"** or **abstain** —
never with A's per-cell confidence. This is map/ask/abstain one level up, and "build for the model you
may use tomorrow": the seam (a validated plan) is unchanged; A and B are two things behind it.

The decisive fact: both got the right number, so a system that sold *answers* could pick B for its
expressiveness. This system sells *traceable* answers — so the spine must be the one where lineage is
structural (A), not extracted (B).

---

## D80 — Lineage IS recoverable from LLM SQL — the B decision, stress-tested
**AGREED** (leaning B, per Gaurav) — prototypes in `prototypes/lineage_stress.py`; visual: "Lineage Receipt" artifact.

Gaurav's pushback on D79: the SQL *is* the recipe and names its ingredients, so read it back and
rebuild the receipt — lineage is a solved problem (provenance). Correct, and demonstrated:

- **Recovering cells from B's SQL works.** Add `array_agg(__row_id)` through the group-by and the
  same 72 cells Approach A produced structurally come back. My earlier "0 cells" was a naive
  extraction bug, not a limit.
- **Stress test, 10 hard shapes** (lookup, rank, ratio, HAVING, top-N, per-region, above-average
  subquery, window-rank, YoY): **numbers 10/10, flat lineage 10/10, precise per-group tree 3/10.**
  The robust method is to REUSE the query's own base predicate (so `IN`/`OR`/ranges survive) and
  re-run it as `SELECT __row_id … WHERE <that predicate>`.
- **The honest limit is over-approximation, not loss.** "Which state used the most?" cites all 36
  scanned rows, not just the winner — a safe SUPERSET. It always over-cites, never misses. Precise
  minimal lineage (just the winner) needs per-shape row-id injection; the tree fires cleanly when the
  query groups on a real column.

**What this settles.** Lineage is not a blocker for Approach B. So B's flexibility (any question,
no grammar to extend) wins the spine, with lineage delivered as: precise tree where the shape allows,
safe superset otherwise, and abstain on the shapes where even the superset can't be trusted (research:
window/DISTINCT/deep-nesting). Research backs the method — ProvSQL / GProM do exactly this by query
rewriting; semiring provenance is the theory.

**Cherry on top:** the "Lineage Receipt" artifact renders the deep answer's tree — 71.66% avg growth
decomposed to 72 cells across 36 states — and surfaces that the mean is skewed by Ladakh (+1,982% off
a base of 5.0 in cell HSD!M15). The receipt is what catches the misleading average.

Next build (not yet done): the safety gate (sqlglot AST + read-only latch), the predicate-reuse
provenance, and the abstain-on-untraceable rule, wired behind the existing validated-plan seam.

---

## D81 — The lineage tree fails 3/10 because it pattern-matched one shape; the fix is the operator tree
**Thinking-out-loud, not built.** Two visual artifacts: "Lineage Receipt", "Lineage Graph".

Why precise per-group lineage was only 3/10: `structured()` recognised a single template
(`GROUP BY <base column>`). A computation's real shape is its **operator tree**, which differs per
query (rank via ORDER-BY-LIMIT, scalar ratio, subquery compare, window PARTITION). Some "misses" have
no tree at all (a lookup/scalar is one flat fact) — the genuine gap is rank/top-N/subquery.

**General fix (research-backed):** stop matching shapes; walk the query's operator tree and propagate
cells per operator — leaf=scan cells, filter passes them up, join = × , aggregate/union = ⊕, derive =
union of operands. Bottom-up, this gives a precise tree for ANY query (semiring provenance, Green 2007;
implemented by GProM via rewriting, ProvSQL via circuits). Approach A gets this free (its plan IS the
operator tree); Approach B must reconstruct it — parse the SQL into an operator DAG, or run GProM-style
rewriting.

**The decision nudge:** the operator DAG is the shared artifact for BOTH lineage and Gaurav's
visualization idea. So the hybrid sharpens: B writes SQL for reach → parse it into an operator DAG →
that DAG drives (a) precise per-node lineage and (b) the clickable graph. Nodes = data (+cells), edges
= operations, click a node → highlight its XLSX cells. Built as a live artifact on the avg-growth case;
it also makes the misleading mean obvious — Ladakh's edge is huge because its 2019 cell is 5.0.

Not wired into the product. Open question for later: build the SQL→operator-DAG parser (sqlglot gives
the AST; map AST nodes → provenance rules), which would take the tree from 3/10 to general.

---

## D82 — Built: the shared-node lineage DAG that doesn't balloon
Prototype `prototypes/lineage_dag.py`; visual "The Shared Node". Not wired into the product yet.

The engine represents a computation as a DAG with two anti-balloon properties, on DuckDB:
- **Sharing** — a shared sub-result (a national total) is ONE node; every user holds an *edge*, not a
  copy. On the share example, `array_agg` materialises **1,332** cell-refs (36 × 37 — the 36 total
  cells re-listed per state); the DAG is **74 nodes**, the total a single node with 36 edges in.
- **Lazy** — a node's cells are computed on demand by walking its edges (`DAG.cells`), never
  materialised up front; opening one state resolves only its branch.

This is ProvSQL's circuit idea (share, don't copy) built with GProM's read-it-from-the-query idea,
and the DAG is the same object as the click-to-cells visualization. Measured, verified, rendered.

**Still open (the honest next step):** the builder here constructs the DAG from the known query shape
(share/growth). The general version parses arbitrary LLM SQL into these nodes (sqlglot AST → operator
DAG → provenance rules) — that is what turns the 3/10 precise-tree into general, and it is the piece
to build to wire this into the product behind the validated-plan seam.

---

## D83 — Built: the SQL → operator-DAG parser (the keystone)
`prototypes/sql_to_dag.py`. Parses an LLM's SQL into a connected operator DAG with lazy, per-node,
verified provenance — the piece that generalises precise lineage and degrades honestly.

**What it does.** sqlglot AST → operator nodes (scan / aggregate / derive / scalar / cte / opaque).
CTEs and scalar subqueries become nodes and are **connected by edges** (a `FROM cte` reference wires
to that CTE's node), so provenance flows through the whole query. Each node computes its contributing
cells **lazily** by re-running its own base predicate (GProM's read-from-the-query) and the AST's
own single-write structure preserves sharing (ProvSQL's circuit).

**Verified correct, not just running.** On DeepSeek SQL: avg-growth → 5 nodes, **72 cells** (36×2,
matches ground truth), flowing through a two-CTE chain; share → 36 cells; lookup → 1 cell. Full
10-shape sweep: **10/10 parse, 0 silent failures.** A window-function query is marked **opaque →
degrade to flat fallback** (cells=0, honest "can't trace precisely"), never a silently-wrong tree.

**Two bugs found and fixed on the way** (both in the parser, logged for the story): sqlglot 30.x
renames the FROM arg to `from_` — my earlier "From traversal quirk" was this wrong key all along; and
the first version built CTEs as *disconnected* subtrees, so the provenance walk never reached the
scans (0 cells) until CTE references were wired as edges.

**Honest remaining gaps.** (1) Rank/max still over-approximate: the DAG holds one scan node with all
candidates, so "which state used the most" cites all 36 scanned rows, not just the winner — a safe
superset, not minimal. Minimal needs expanding the max/limit operator. (2) Only the single base table
is modelled; cross-table joins would need the × rule. (3) Not wired into the product — this closes the
research arc (D79–D83); wiring it behind the validated-plan seam is the next build when we choose to.

This is the object everything converged on: it powers precise lineage AND the click-to-cells graph,
from one parse of the SQL.

---

## D84 — The SQL escape hatch, wired into the product behind the validated-plan seam
**LOCKED** — `src/sql_dag.py`, `src/sql_gate.py`, `src/sql_path.py`, wired in `src/ask.py`.

Deep questions the typed planner can't express (average-of-per-group, period-over-period) now get a
second path, *behind the same seam*: a frontier model writes SQL → **validate** (sqlglot AST allowlist
+ read-only) → **execute** → recover lineage from the **operator DAG** (D83). The seam holds: what runs
is always a validated artifact, the model still never emits a digit (DuckDB computes it), and the answer
ships **only if its cells are traceable** — no lineage, no answer, the honest abstention stands.

**Where it fires (conservative).** Only when the typed system gives up: `find_unsupported` (fires on
zero e2e questions) or the planner returning `unsupported_operation`/`planner_failed`. Everything the
typed path handles is untouched.

**Verified.** Via `ask()` with a DeepSeek planner: "average diesel growth across all states" and
"national diesel change 2023-24→24-25" both answer with **72 source cells** cited, rendered by the
existing `Trace` + click-to-open `SheetViewer` (no frontend change needed). Scalar answers only for now;
multi-row lists defer. In the browser the hatch needs a DeepSeek key entered (the weak local model
can't write reliable SQL) — with no key it simply keeps abstaining.

**Found on the way (not caused by this change):** the DeepSeek planner *occasionally* mis-plans a share
question (returns a group-by-state QueryPlan → v1='BIHAR'), and `eval_e2e` crashed on `float('BIHAR')`.
Hardened the harness to treat a string-where-number as a mismatch, not a crash. **Confirmed** in a
re-run: e2e 14/15, the one failure being that flaky Bihar-share mis-plan (answer `v1='BIHAR'`, a typed
QueryPlan — NOT an escape answer). Pre-existing planner non-determinism (15/15 in earlier runs),
orthogonal to the escape hatch; the escape hatch itself verified separately (deep questions → 72 cells).

**Honest limits carried in:** rank/max lineage over-approximates (safe superset); single base table only;
window functions mark the DAG opaque → the hatch abstains rather than show a wrong tree.

---

## D85 — Median routes to SQL; contiguous cells collapse into ranges
**LOCKED** — `verify.UNSUPPORTED` (+stats), `present.cell_ranges`, `api` (ranges), `Trace`/`SheetViewer`/`api.ts`.

**1. Stats the typed grammar can't do now route to the escape hatch.** The planner was silently mapping
"median" to "avg". Added median/standard-deviation/variance/percentile/quantile/correlation to
`UNSUPPORTED`, which already routes to the SQL path — so "median diesel across states" now writes SQL
and returns the TRUE median (1,109.66, matches an independent DuckDB `median()`), cited to 36 cells.

**2. Lineage references collapse contiguous cells into blocks.** A citation list of 36 separate cells
(`S10, S11, …`) now shows as **5 ranges** (`S10:S19, S22:S29, …`) — runs break exactly where a
section total sits between entity rows, so each range is a real contiguous block. `present.cell_ranges`
computes them, the API ships them per slot, `Trace` renders them as chips, and clicking one opens the
sheet with the **whole range highlighted** (SheetViewer parses `S10:S19` → highlights every cell,
scrolls to the first). Fewer references, and the sheet lights up the block at once — closer to how a
person would select the range in Excel.

Verified via `/api/ask` with the env key: share answer's denominator = 36 cells → 5 ranges; median →
escape hatch, correct value. 69/69 gates, 4/4 record-QA, tsc clean.

---

## D86 — Removed the legacy Streamlit; fixed the shipping requirements
**LOCKED** — deleted `src/render/streamlit_app.py`, `src/render/confirm_app.py`.

Streamlit was superseded by the React client (D63–D65) and only lingered as dead entry points and two
stale servers running old code. Removed the two files, killed our stale servers on :8502 and :8507
(SIGKILL — Streamlit ignores SIGTERM), and left the unrelated `RAG_Explained` server on :8501 alone
(a different project on the machine). Dropped `streamlit` from `requirements.txt` and updated the run
instructions in `README.md` / `CLAUDE.md` to the React + FastAPI flow.

**Found while cleaning:** `requirements.txt` was missing three deps the *shipped* product needs —
`fastapi`, `uvicorn`, `sqlglot` (the API and the SQL escape hatch). A fresh `pip install -r` would have
failed to run the app. Added them. The React `frontend/` keeps its own `package.json`.

Nothing imported the deleted modules; `api`, `ask`, `sql_path` and both deck renderers still import
clean, and the backend stayed up through the change.

## D87 — A breakdown asks which group; it never voices one arbitrarily

**AGREED, then LOCKED (pre-push review).** A query plan with `group_by` and no `limit` is a
breakdown ("petrol *by year*") — the SQL returns one row per group. The narration contract
(planner rule 3) gives it a single `{v1}` label + `{v2}` value slot, so it can only voice one
group. `execute` was calling `.fetchone()` and presenting the first row as the whole answer:
a confident, clean-citation, wrong-shape answer — the exact failure class D9 exists to prevent.

Fixed in `execute.py`: fetch all rows; if `group_by` is set and more than one row comes back,
return `status="clarify"` naming the group column, the count, and the first six labels as
`scope_options` buttons. A single filtered result (1 row) and a "which X is highest" (`limit=1`,
1 row) both skip the branch and answer as before.

**Rejected:** actually rendering the breakdown as a multi-row table. That is a real feature —
N citation sets, a new Answer shape, frontend rendering — not a bug fix, and out of scope for a
pre-push patch. Clarifying is the honest minimal move: better to ask than to lie about the shape.

**Verified:** hand-built breakdown plan (`group_by=['year']`, no limit) over the PPAC db →
`clarify`, "That breaks down into 18 groups by year… which year?"; a filtered single-value plan
still answers 190,626.65. All four free eval suites still pass (69+8 gates, 8 corpus, 3 ingest,
4 record-QA).

## D88 — The measure's unit comes from the spec, not a PPAC constant

**AGREED, then LOCKED (pre-push review).** `execute.py` carried `UNITS = {"value": "thousand
metric tonnes"}` — a PPAC-era hardcode whose own comment said "moves to the spec with D16". D16
landed; this didn't. Any workbook whose measure column is named `value` inherited PPAC's unit:
a Union Budget ₹-crore total came back labelled "thousand metric tonnes", and `/api/ask` shipped
that to the client.

Fixed in `execute.py`: the three unit lookups now read `catalog.unit if c.metric_column ==
catalog.measure else None`. `catalog.unit` and `.measure` already flow from each file's spec
(`build(db, spec.table, spec=spec)`), which is exactly what `sql_path.py` already used — so the
SQL path and the grammar path now agree, sourced from one place. `UNITS` deleted.

**Verified:** built each catalog through its spec — PPAC still `unit='thousand metric tonnes'`,
budget `unit=None`, climbing `unit=None`. An end-to-end `execute` of a budget total returns
`unit=None` (was the leaked PPAC unit). Free eval suites unchanged (69+8, 8, 3, 4).

## D89 — Deleted the orphaned planner scoreboard rather than resurrect it

**AGREED, then LOCKED (pre-push review).** `tests/eval_planner.py` was dead twice over: it did
`from plan import COMPILER_DEFAULTS` (a symbol removed when defaults moved to `Catalog.defaults`),
and it read `c["kind"]`, `c["filters"]`, `c["group_by"]` from `questions.yaml` — fields that file
no longer carries (its cases are `q / status / cites / values`). Running it crashes on the first
case (`KeyError: 'kind'`). Nothing references it — not the README suite table, not docs, not code.

**Rejected: fix it.** Repairing it would mean re-authoring expected *plan structures* (kind,
filters, group_by, sort, limit) for all 15 cases — inventing the ground truth — and it is a paid
DeepSeek eval, so no fix could be verified without spending real API budget, violating the
project's "verify, don't assert" rule. A broken orphan an interviewer can run and watch crash is
worse than its absence. The live-model story is carried by `eval_e2e.py` (same 15 cases,
end-to-end); the deterministic planner behaviour is covered by the 69-case gate suite.

**Verified:** file removed; the four free eval suites are unaffected (they never imported it).

## D90 — The table name is validated at the Spec seam; injection is unconstructable

**AGREED, then LOCKED (pre-push review).** `/api/confirm` accepted a browser-supplied spec whose
`table` flowed **unquoted** into `DROP TABLE {t}`, `CREATE TABLE {spec.table}`, two `INSERT`s
(`ingest_spec.py`) and `DESCRIBE`/`SELECT {table}` (`catalog.py`). DuckDB runs `;`-separated
statements, so `table = "x (i INT); DROP TABLE cell_map; --"` executed a `DROP` of the citations
table. Reproduced on a throwaway db (3 rows → table gone) and through the real HTTP endpoint.

Fixed where it is **mechanically impossible to get wrong** (D9): a `field_validator` on
`Spec.table` rejecting anything but `[A-Za-z_][A-Za-z0-9_]*`. Both entry points construct a `Spec`
— the API via `model_validate`, the CLI via `Spec(**yaml)` — so an injectable spec cannot be built
anywhere; there is no caller to forget a check. `propose()` now prefixes a numeric-leading stem
(`2024` → `_2024`) so a legitimate filename never trips the gate. `confirm()` catches the
`ValidationError` and returns `400` with the reason, not a `500` stack trace.

**Rejected — validate *every* identifier strictly (my first instinct).** Verifying against the real
files killed it: the climbing file's `measure` is `% vs pivot (now)`, a legal *double-quoted*
column name. A blanket identifier rule would refuse a working file — overfitting the fix to the
attack. Only `table` is unquoted, so only `table` needs the strict rule. Column names are a
narrower vector (an embedded `"`), tracked separately.

**Parked, named honestly:** (1) column names built from meaning-fields are double-quoted but not
`"`-escaped in `ingest_spec.py`; (2) `spec.file` is not basenamed on the `/api/sheet` workbook
branch (path traversal, review finding #6). Neither is closed by D90.

**Verified:** injection string rejected at `Spec()`, through the endpoint as a `400`, cell_map
intact; `consumption`, all three real specs (incl. climbing's `% vs pivot (now)`), and a
numeric-leading stem all pass; ingest reproduces 2210 rows · 12882 receipts; free suites 69+8/8/3/4.

## D91 — Closed the other two trust-boundary holes at their choke points

**AGREED, then LOCKED (pre-push review).** The two vectors parked under D90, now fixed the same
way — validate where the value must pass, not at each call site.

- **Path traversal via `spec.file` (finding #6).** The API joins `DATA / spec.file`; every site
  basenamed it except `/api/sheet`'s workbook branch (`api.py:293`), so a spec with
  `file: ../../x.xlsx` read outside `data/`. Fixed with a `field_validator` on `Spec.file` that
  requires a bare filename (`v == Path(v).name`, and not `.`/`..`) — traversal is unconstructable
  through either entry path. `api.py:293` also basenamed now, matching its sibling (defense in depth).

- **Column-name break-out.** Column names reach `CREATE TABLE` as double-quoted identifiers; an
  embedded `"` escapes the quotes. They converge from two sources — the crosstab branch uses
  `spec.entity/period/measure` (browser-controlled), the record branch uses **workbook header
  cells** (`clean_label` strips footnotes and whitespace, not quotes). A `Spec` validator can't see
  the workbook-derived ones, so the guard lives in `_write` (`ingest_spec.py`) — the single point
  where every column name meets the CREATE — rejecting any name containing `"`.

**Rejected — escape `"` → `""` instead of rejecting.** Escaping would store a literal `"` in the
column name, and `catalog.py`'s own `f'"{name}"'` re-quoting would then break at read time. A `"`
in a column name is never a real workbook (even `% vs pivot (now)` has none), so refusing it is
self-contained and needs no downstream change.

**Verified:** four traversal strings rejected at `Spec()` and `/api/confirm` returns 400; a header
of `x" DOUBLE); DROP TABLE cell_map; --` rejected at ingest; all three real specs validate; PPAC
(2210 rows, crosstab) and climbing (29 rows, record/header-derived columns) both ingest; free
suites 69+8/8/3/4.

## D92 — Deck authoring uses the caller's model; any OpenAI-class API is a registry entry

**AGREED, then LOCKED (pre-push review).** `deck_agent.narrative()` and `story()` took a `planner`
argument and ignored it, calling `ollama.chat(model="llama3.2")` directly. So `/api/deck` built the
caller's cascade from their provider/key (D40) and then arranged the story on a *different*, local
model — or silently fell back to deterministic if Ollama wasn't running. "What runs is what the
caller sent" was violated at exactly the step the caller thinks they're paying for.

Fixed by giving the planner family a `chat_json(system, user)` method — a free-form JSON turn on the
already-configured client/key/model, beside the existing `.plan()`. `DeepSeekPlanner` (the
OpenAI-compatible class) reuses its client and `_bill`; `OllamaPlanner` uses the local lib;
`CascadePlanner` delegates cheapest-first and returns `None` if no tier answers, so `deck_agent`
keeps its deterministic fallback. `narrative()`/`story()` now call `planner.chat_json`; `deck_agent`
no longer imports `ollama` at all.

**Generic provider.** Added an `openai` entry to `PROVIDERS` (base_url `api.openai.com/v1`, env
`OPENAI_API_KEY`). It is also the template: Groq, Together, Fireworks or a local vLLM are the same
four-line entry with a different base_url/env — the class never changes (D22). Models are fetched
live from `/models` when a key is present; prices omitted so `_bill` reports nothing rather than
inventing one (D26). `.env.example` documents the new key.

**Verified:** a fake planner proves `narrative()`/`story()` route to `planner.chat_json` (title,
order and closing all come from it); `planner=None` still yields the deterministic story; a full
deck builds end-to-end (4 findings, 48 KB); `make_planner("openai", ...)` constructs through the
generic class pointing at `api.openai.com` with `chat_json` present. Free suites 69+8/8/3/4.

## D93 — Eval scoreboards exit nonzero on failure

**AGREED, then LOCKED (pre-push review).** All five eval scripts printed a `passed/total` line and
always exited 0, so a red line in the output still returned success — CI or a pre-push hook could
not catch a regression. Each now ends with `sys.exit(0 if passed == total else 1)`. They stay
scoreboards (they still print every case), but a failure now fails the process.

**Verified:** the four free suites exit 0 on a clean run; a copy with the pass-condition forced to
an impossible target exits 1.

## D94 — Raw OpenAI/Ollama SDKs, not LangChain/LangGraph

**AGREED, then LOCKED (pre-push review).** I evaluated LangChain and LangGraph and chose the raw
`openai`/`ollama` SDKs plus Pydantic. Those frameworks exist to orchestrate agent loops, where the
model calls tools and keeps going until it is done. This system barely loops: the model fills in one
validated JSON plan (D5, D22), and then deterministic code compiles it, executes it, and checks
every number against its source cell. The value, and the guarantee that nothing is fabricated, sits
in the plain code rather than in an orchestration layer. A graph engine would be machinery bolted
onto a straight line.

**Rejected: adopt LangChain to signal familiarity with the tooling.** For a take-home graded on
judgment, a framework used where the raw SDKs already suffice reads as resume-driven rather than
problem-driven. The stronger signal is knowing the tool well enough to turn it down, and being able
to say where the line is. The three things LangChain would give me, I already hold in a defensible
form. Provider swapping is a four-line registry dict (D22). Structured output with a retry is
roughly fifteen lines: validate, repair once, otherwise abstain. And reading raw `usage` is what
surfaced the 18x cost cut (README §4.8), which a framework abstraction would have hidden.

**Where the decision flips:** if the product becomes a real multi-step agent that picks among many
workbooks, chains questions, searches across documents, and needs checkpoints or a human in the
loop, then LangGraph's state model would be worth its weight. That is a different product from
structured-data QA with a hard traceability guarantee. Recorded in README §4.9.

## D95 — The planner retries a stochastic miss before abstaining

**AGREED, then LOCKED (pre-demo).** A user hit "I can't answer this from the file — I could not
build a reliable query" on a question the data plainly supports ("Which state was highest in
2024-25?"). The CLI answered it 5/5; the failure was a one-off. Cause: the model is stochastic even
at `temperature=0`, so it occasionally emits a plan that trips a gate (coverage / compile), and with
a single configured tier the cascade had nothing to escalate to — it abstained on the first miss.

Fixed in `CascadePlanner.plan`: wrap the tier loop in a bounded retry (`ATTEMPTS = 3`). A stochastic
miss now re-draws instead of surfacing as a refusal; only a *consistent* failure across all attempts
abstains. A legitimate `Abstain` (the model deciding the data can't answer, e.g. "no tax column")
still returns immediately via the in-loop `return`, so retries are never spent on a real refusal and
add no cost or latency on the happy path — they fire only when the run would otherwise have failed.

**Rejected: prompt-tuning the planner to be "more careful."** That is the "ask the model nicely"
anti-pattern (D9) — it cannot make a stochastic process reliable. A retry is mechanical: p(miss)³
instead of p(miss).

**Verified:** the demo question ran **15/15 answered, 0 abstained** on the uploaded spec; a genuine
refusal ("How much tax…") still abstains in ~1s (one attempt, right reason), not after three
retries; gate suite 69+8 still passes.
