# The bug journal

Every bug found while building this, what it looked like, and what it changed. Kept because
"judgment" is one of the things being graded, and the honest version of judgment is *what went
wrong and what you did about it*.

Grouped by the lesson rather than chronologically — several bugs turned out to be the same
mistake wearing different clothes.

---

## 1. Wrong answers that look exactly like right ones

The failure mode the whole architecture exists to prevent — and it still got through four times.

### 1.1 The wrong aggregation · **the worst one**
*"Which region used the least diesel in 2020-21?"* → **SOUTH, 15.98**.

South India contains Tamil Nadu and Karnataka; 15.98 thousand tonnes is absurd. It is
**Lakshadweep's** figure. The plan used `min(value) GROUP BY region ORDER BY 2 ASC`, which finds
the region *containing* the smallest single row, not the region with the smallest total. Correct
answer: **NORTH EAST, 1,775.69**.

It passed **every** gate. Coverage saw all filters present. The compiler saw valid columns.
`check_lineage` passed, because for `min` the rule is "the answer is one of the cited values" —
and 15.98 genuinely was.

**Caught by:** domain knowledge. Not by any check.
**Fixed:** `min`/`max` as the metric with `group_by` + `limit` is now rejected outright (D37),
plus a prompt rule. Structural first, prompt second.

### 1.2 The eval scored that wrong answer 9/9
Worse than the bug. The suite asserted *status* and *citations* — never the **number**. A
scoreboard that passes wrong answers is more dangerous than no scoreboard, because it converts an
unknown into a false assurance.
**Fixed:** expected values in `questions.yaml`, checked to 0.01 (D38).

### 1.3 The product double-count
`MS` and `HSD` are subsets of `ALL`. Summing without a product filter gives Delhi FY2015-16 as
**7,406.53** instead of **4,996.70** — 48% too high.
**Caught by:** the eval harness, on its first real run.
**Fixed:** `product = 'ALL'` injected by the compiler when unstated (D24).

### 1.4 A refusal that stated a falsehood
*"What was Gujarat's petrol consumption in 2030-31?"* → *"the table has data only up to
2023-24"*. The file runs to **2025-26**, and the catalog says so.

D1 was enforced rigorously for answers and left wide open for refusals — the `reason` field was
free prose from the model.
**Fixed:** the model picks a reason *code*; we compose the sentence from the catalog (D35).

---

## 2. Checks placed where the evidence no longer exists

Four bugs, one mistake: a check that runs too late to see what it needs.

| bug | why it could not work |
|---|---|
| `find_ambiguity` inspected the *plan* | by then the model had already resolved "Uttar" invisibly, or abstained — either way the ambiguity was gone |
| absent-concept detection left to the model | `llama3.2:3b` answered the tax question instead of refusing; every column it named was real, so no downstream gate could see the problem |
| the coverage gate lived only inside `CascadePlanner` | with no API key `get_planner()` returned a bare planner, so **answer quality depended on whether an env var was set** |
| the slot-contract check ran at render time | `KeyError: 'v3'` — a crash, when it should have been an escalation |

**Fixed:** ambiguity and absent-concepts moved *before* the planner into `src/ask.py`; the cascade
now validates a plan by attempting to compile it; `get_planner()` always wraps in
`CascadePlanner` (D28, D33, D36).

> A check belongs at the earliest point where the information it needs still exists.

---

## 3. String matching that was too clever, or not clever enough

Five bugs, all in code I wrote to be careful.

- **`"east" in "least"`** — the coverage gate demanded a `region` filter for *"which region used
  the l**east** diesel"*. Substring matching without word boundaries. → `(?<![a-z0-9])…(?![a-z0-9])`
- **`row_kind` has a value called `state`** — so *"Which **state** consumed the most petrol"*
  demanded a `row_kind` filter and abstained on an answerable question. A collision between one
  column's *name* and another column's *value* (D34).
- **Trailing punctuation, twice** — `2019-20.` did not match `2019-20`, so the digit check flagged
  a year the user had typed. Fixed once in the eval, then again in `verify.py` with
  `\d(?:[\d,\-/.]*\d)?` — must end on a digit.
- **`"uttar"` does not replace `"Uttar's"`** — the clarification buttons silently did nothing.
  Case-insensitive `re.sub`.
- **`Region Total` listed as a state** — ingest set `state = label` for total rows, so the planner
  could have filtered `state = 'Region Total'`. Found by *reading the generated catalog*, which
  reading `ingest.py` had not revealed (D19).

**Every one of these caused a wrong refusal** — the system declining a question it could answer.
That is the over-refusal failure from Lesson 0.5, and it is what a too-eager gate actually costs.

---

## 4. The test was wrong, not the code

Three times. Worth its own section because the instinct is always to fix the code.

- **`questions.yaml` demanded `product: ALL` from the planner** — while D24 assigns that to the
  compiler. Removed it; the model then *supplied* it, and the test failed again as "unexpected".
  Both plans were correct; `==` could not express that. Fixed by splitting the check into
  `missing` (strict) and `extra` (allowed only if it equals a compiler default).
- **The digit check flagged legitimate years** — the user asked about 2019-20, so the narration
  may say 2019-20.

> A failing test is a disagreement between two pieces of your thinking. Which one is wrong is a
> question, not an assumption. Final tally: six failures, three code, three test.

---

## 5. I believed my own logs

The eval printed traces *during* `plan()` but the question header *after*, so every escalation
appeared under the previous question. I concluded Ollama was non-deterministic, spent three tool
calls chasing it, and it was fine the whole time.

**Fixed:** print the header first.

> A log that can be misread will be misread — including by the person who wrote it.

---

## 6. Money leaks you cannot see without measuring

- **The cost estimate was 4x wrong.** I predicted $0.0007/question; the real figure was $0.0027.
  Metering showed why: **reasoning tokens were 99% of the bill** — ~4,000 output tokens before a
  ~150-token plan. One parameter (`thinking: disabled`) made it **18x cheaper with an identical
  6/6 score**. Without the meter I would have optimised the catalog — the input side, already 99%
  free (D26).
- **Expanding a citation panel cost money.** Streamlit re-runs the whole script on every widget
  interaction, so opening a disclosure triangle fired a fresh planner call. Measured live:
  `$0.00000 → $0.00008`. Fixed by caching the `Answer` in session state (D41).

---

## 7. Interfaces that could be misused quietly

- **`provenance_sql()` accepted a grouped plan with no `group_values`** and returned citations for
  all 36 states instead of the 10 in NORTH. Technically "rows the query scanned", completely wrong
  as an answer to "where did this number come from?" → now raises (D29).
- **A self-contradictory plan returned zero rows and blamed the data.**
  `state = BIHAR AND row_kind = 'all_india'` can never match — total rows carry no state. The
  model reaches for `all_india` whenever a question says "total". → now rejected in the compiler
  (D44).
- **`st.session_state.q` cannot be set after its widget exists.** The example buttons sit above
  the input and worked; the clarification buttons sit below it and crashed. → `on_click` callbacks.

---

## 8. Honest limits, found by using it

*"What is Bihar's share of total consumption of petrol"* is a **ratio**. The plan format has one
metric and no division, so no plan can express it — the refusal was correct but said *"matched no
rows"*, which blames the data for a limit of the system.

Now a distinct refusal (D44):

> answering that needs a ratio between two aggregates, which this system cannot express yet — it
> computes one aggregate per question, not ratios between two. Ask for the parts separately and
> they will each be traceable

"The file has no tax data" means **stop**. "I can't divide two numbers yet" means **rephrase** —
and the rephrasing works.

---

## 9. The mirror bug: inventing a filter

*"What is Bihar's share of total consumption of petrol"* — no year in the question — came back
scoped to **2024-25**. The model added a constraint nobody asked for, and answered a narrower
question than the one asked, with perfectly valid citations for the wrong scope.

`coverage_gaps` catches a **dropped** filter. Nothing caught an **invented** one. The check had a
mirror image and I had only built one half.

**Fixed:** `overfilter_gaps()` — any filter whose value the question never names (by label or
alias), compiler defaults exempt. Runs in the cascade beside coverage, so it escalates rather
than shipping.

**And a knock-on:** `check_digits` crashed on *"…in 2019-20"* — a narration may legitimately
restate a *filter* value, which is not a computed number. Allowed now; computed numbers still
only arrive through slots.

---

## 10. Silently answering a broader question

*"What share of consumption does Bihar contribute to?"* answered **2.76%** — quietly summing
**18 years**. Every citation was correct; the scope was not the one asked for.

The tell was in the trace: **655 source cells** for a question about one state.

Two omissions, only one benign. `product` defaulting to `ALL` is fine — `ALL` is a real value in
the sheet. `year` left unfiltered is not: it does not *select* anything, it **aggregates across
the whole dimension**. A default value is a choice within a dimension; aggregating across one is
a choice about scope.

**Fixed:** scope dimensions are inferred from label shape (period patterns), and an
unconstrained one triggers a clarification with buttons (D48). Cost: 2.76% over 18 years vs
2.79% for 2024-25 — the assumption was changing the answer.

---

## 11. A ratio that was the wrong ratio

*"How much % did petrol consumption in Bihar increase in 2025-26 compared to previous year?"*
answered **3.14%** — Bihar's 2025-26 petrol divided by **ALL INDIA's** 2024-25 petrol, narrated
as growth. Correct: **8.23%**.

Two model errors at once: the denominator dropped `state`, and the op was `divide` rather than
`percent_change`. Every citation was valid. The lineage check passed — each measure genuinely
reconstructed from its cells. Only the *relationship between them* was nonsense.

My denominator check counted **how many** filters were dropped (one — `state`) and never asked
whether the shape made sense for the operation.

**Fixed:** `check_derivation_shape()` — a `divide` requires the two measures to agree on every
shared column; a `percent_change` requires identical filters except the period (D50).

**And on the way there, a self-inflicted one:** `overfilter_gaps`, added an hour earlier to catch
invented filters, flagged `year=2024-25` as invented because the question said *"previous year"*.
A check built to catch invention started refusing legitimate resolution — the fourth false
positive from this family.

> A new gate needs its own false-positive test, not only its true-positive one.

---

## 12. A typo refused an answerable question

*"what % change does **Maharastra** shown in Petrol consumption last year"* — one missing `h`.

Both models resolved it correctly to `MAHARASHTRA`. `overfilter_gaps`, added that same session to
catch *invented* filters, flagged it as invented because the label was not in the question
literally. Both tiers escalated; the cascade abstained on a question the file answers fine.

**Fixed:** fuzzy fallback at 0.82. `maharastra`/`maharashtra` scores 0.95; `punjab`/`puducherry`
scores 0.25. Typos clear it, unrelated names do not.

**Fifth false positive from this family** — after `"east" in "least"`, the alias/default case,
`row_kind`'s `state` value, and relative time references. Every one refused an answerable
question. The pattern is now unmistakable: I kept testing that a gate *fires* and never that it
*stays quiet*.

**And a second bug in the same question:** *"last year"* silently resolved to 2024-25 vs 2023-24
(**7.44%**) when the file runs to 2025-26 (**6.29%**). Contrast *"in 2025-26 compared to the
previous year"*, which is anchored by an explicit period and is fine. Now asks when unanchored
(D52).

---

## 13. The gates were the biggest source of bugs

Five bugs — a third of the total — came from checks I added to make the system safer. Every one
**refused a question the system could answer**. Listed together the pattern is obvious:

| gate | false positive |
|---|---|
| coverage | `"east"` inside `"least"` |
| coverage | `row_kind` has a value literally named `state` |
| coverage | an alias mapping to the compiler's own default |
| overfilter | `"previous year"` looked like an invented filter |
| overfilter | the typo `"Maharastra"` looked like an invented state |

**Root cause: a testing habit.** Each gate was tested for what it should *catch* and never for
what it should *ignore*.

**Fixed:** `tests/eval_gates.py` — 63 cases, **26 should-fire and 37 should-stay-quiet**. The
gates are pure functions, so the suite runs in milliseconds and costs nothing; it can be as large
as the input space needs, while the end-to-end suite stays at 15 because it costs money.

### And the suite found a fresh bug immediately

Everything passed on the first run — suspicious, not reassuring, since the cases were written
after each fix. Probing for situations I had *not* already met:

```
ANDHRA PRADESH / MADHYA PRADESH = 0.86   ← over the 0.82 typo threshold
```

So a plan filtering `MADHYA PRADESH` for a question about **Andhra** passed as a typo. Too strict
refused "Maharastra"; too loose accepted "Madhya" for "Andhra" — **no threshold fixes both**.

**Fix:** require the filter's value to be the label the question matches *best*, not merely one
that scores above a bar.

> A gate is not a threshold, it is a comparison. "Close enough" admits the wrong answer whenever
> two right answers are close to each other.

---

## 14. The same bug, in a new disguise

Replacing the threshold with Gaurav's three-way resolver (none / one / many) brought back
**`"east" in "least"`** — this time through fuzzy matching rather than substring:

```
"least" vs "east"  =  0.89     ← above the 0.82 threshold
```

The original fix was word boundaries. Word boundaries were still in place; the *new* matching
path went around them.

> A fix protects the code path it was written for. A new path needs its own.

**And a second:** `MADHYA PRADESH` was accepted for a question reading *"Andhra Pradesh petrol"*,
because the word `pradesh` ties five states and the tie branch excused any tied candidate.

**Both fixed by one rule:** a label spelled out in full settles that column — no word-level
guessing when the question could not have been clearer. Plus `FUZZY_MIN = 5`, since fuzzy exists
for typos (which preserve length) and on short words finds unrelated English instead.

Caught by the gate suite in **seconds**, not by a user hitting it a week later. That is what the
suite is for.

---

## 15. A comment is not a defence

`named_in`'s docstring said *"two definitions of 'stated' would drift apart"* — and I then wrote a
second one, by leaving value aliases out of it. `product = HSD` from the word "diesel" read as
`stated` to one caller and `invented` to the other. The memory tests failed on **turn 1**, before
a follow-up was even involved.

> A comment warning about a hazard is not a defence against it. Only a single implementation is.

**And a cache key that invalidated itself.** The Streamlit key included the conversation history,
so answering a question changed the key that had just been set for it — cache miss on rerun,
`ask()` ran twice, the question appeared twice in the sidebar, and one click cost two API calls
(`$0.00000 -> $0.00012`, measured).

> A cache key must not depend on state the cached operation mutates.

Both caught within minutes — the first by the free gate suite, the second by watching the spend
counter move when it should not have.

---

## What the tally says

| found by | count |
|---|---|
| the eval harness | 4 |
| driving the UI by hand | 3 |
| reading generated output (the catalog, the trace log) | 2 |
| **domain knowledge — knowing India** | **1, and the worst one** |
| Gaurav asking "should it have asked me?" | 2 |
| Gaurav saying "this does not look right" | 3 |
| code review | 4 |
| the two-sided gate suite | 3 |

**The single most valuable check was a person who knew that South India uses more than 15.98
thousand tonnes of diesel.** No amount of schema validation substitutes for someone who can smell
a wrong number — which is the argument for choosing a dataset you understand, made concrete.

---

## Metadata that names nothing is invisible — it does not fail, it just stops existing

**Symptom.** The first `POST /api/ask` refused an answerable question: *"What was Gujarat's diesel
consumption in 2019-20?"* → *"I could not build a reliable query."* The identical question through
Streamlit answered correctly.

**Cause.** Making the catalog spec-driven meant `build()` took its annotations from
`specs/ppac.yaml` instead of the module constant. That spec's annotations had been written by the
model, keyed by the sheet's **header label** — `STATE/UT` — rather than by the column name `state`.

No key matched a column, so no annotation was ever read. Which silently deleted
`value_aliases: {diesel: HSD, petrol: MS}`. With no alias for "diesel", `overfilter_gaps` was
**correct** to call `product=HSD` an invention, and the refusal was correct given what the catalog
said. Every layer behaved properly. The catalog was just quietly emptier than anyone thought.

**Fix.** Two parts:
1. The hand-curated annotations moved into `specs/ppac.yaml`, where a comment in `catalog.py` had
   said they belonged since D16. `build()` now has no PPAC-specific default in the spec path.
2. `Catalog.unknown_annotations` — annotation keys naming no column are collected and surfaced
   through the API. Same shape as `verify_absent()`: a claim about the data, checked against the
   data.

**Lesson.** Three separate bugs now share one shape: `absent_concepts` backwards on the Budget
(D58), the missing `section_dimension` field (D61), and this. In each, model-proposed or
human-supplied metadata was *absorbed without being checked against the schema it describes*, and
in each the result was a confident, plausible, wrong-in-one-direction system — never a crash.

Metadata that names nothing does not error. It just isn't there.

**Found by:** running the new endpoint against a question whose exact answer was already known
(`HSD!M42` = 5,607.58). A smoke test asking "did it return 200" would have passed.

---

## `or None` turned "this file declares nothing" into "use the other file's rules"

**Symptom.** The Union Budget, ingested through the new React screen, came back with
`absent_concepts = ['tax', 'taxes', 'revenue', 'price', ..., 'gdp', ...]` — a list that would hard-
refuse, before any model ran, essentially every question the Union Budget exists to answer.

**Cause.** One idiom:

```python
build(db, s.table, annotations=s.annotations or None, absent=s.absent_concepts or None)
```

`or None` was meant as "if the spec has nothing, let the default apply". But the default is
`catalog.py`'s **PPAC** constants. So a confirmed workbook that declared nothing silently inherited
the fuel file's semantics — its annotations *and* its absent-concepts list.

The annotations half was visible (`unknown_annotations` reported `['product','region','state','year']`
naming no Budget column, which is what surfaced it). The absent half was not visible at all: a
refusal list that is too long produces no error, only questions that quietly stop being answerable.

**Fix.** Pass them verbatim. A confirmed spec's empty list means **empty** — we do not know what a
workbook lacks, and guessing on its behalf from a different file is worse than not guessing.

**Lesson.** This is D58's bug through a new door. There, a model proposed `['revenue','expenditure',
'deficit']` for the Budget; here, a fallback did the same thing by inheritance. Defaults are a claim
about the data, and the claim needs to be about *this* data.

`x or DEFAULT` is not "unset means default" — it is "**empty** means default", and for a
declaration, empty is a real answer.

**Found by:** reading `/api/workbooks/{id}` after ingesting a second workbook. No test covered two
workbooks, because until this session there could only ever be one.

---

## One file's vocabulary, worn by another — five sightings of one bug

Gaurav pushed back twice: *"nothing should be inherited from one file or another file."* He was
right, and the sweep found the same failure in five places. Every one produced a working system
that was quietly wrong.

| where | the borrowed thing | what it did |
|---|---|---|
| `plan.py` | `COMPILER_DEFAULTS = {"product": "ALL", "row_kind": "state"}` | every gate consulting it was specific to one workbook |
| `compile.py` | contradiction check written around `state` | silently no-ops on a file whose rows are budget lines |
| `planner.py` | four hand-written PPAC few-shot examples | teaches every model a schema the file may not have |
| `catalog.py` | `ANNOTATIONS` / `ABSENT_CONCEPTS` module constants | the Budget declared it had no revenue |
| `ingest_spec.py` | `row_kind = "state"` | budget lines recorded as states |

**The one that cost the most was the safest-looking.** Making the row-kind vocabulary generic
(`entity` / `subtotal` / `grand_total`) broke seven end-to-end cases, and the reason was not the
change — it was that the *suites still read `data/warehouse.duckdb`*, the database built by the
hand-written `ingest.py`. Its rows still said `row_kind = 'state'`, so the new default matched
nothing. **The tests had been testing a database the product does not ship.** Fixed by routing
everything through `workbook.load()`: a workbook is a spec plus the database that spec produced,
and there is no other way in.

**Three further bugs surfaced only once the vocabulary changed**, each hidden by the old words:

1. **The spec still *described* the old vocabulary.** `row_kind`'s annotation said *"state = one
   state; region_total = zonal subtotal"*, so the planner faithfully emitted `row_kind="state"`
   against a table that no longer had it. Two cases matched zero rows. Fixed by moving that
   description into code — `row_kind` is produced by ingest for every workbook, so what it means is
   structural and cannot live in a per-file spec where it can only rot.
2. **Underscore was not a word character.** `(?<![a-z0-9])total(?![a-z0-9])` matches *inside*
   `grand_total`. The old labels hid it: only `region_total` contained the word.
3. **`row_kind` took part in ambiguity detection.** "Delhi's **total** consumption" was reported as
   ambiguous between `grand_total` and `subtotal` — two words the user did not say and could not
   have meant, because this pipeline invented them. Columns are now marked `internal`, and internal
   columns are excluded from every check that asks *"did the user name this?"*, where the answer is
   always no and a false yes is expensive.

**And one caught by a generated feature, not a test.** Starter questions are now generated from the
open workbook's labels. The endpoint returned an empty list — because `api.py` built its catalog
with `annotations=` and `absent=` but not `spec=`, so `entity`, `period`, `measure` **and
`defaults`** all sat at their fallbacks. The API had been running with **no `product = ALL`
default**: the fuel file's three sheets overlap, and summing them triples every figure (688,573
against a true 223,480). An empty suggestion list is what exposed a live double-counting bug.

**Lesson.** A default, an example, a description and a label are all *claims about the data*. The
question is never "is this claim reasonable?" but "is it a claim about **this** file?" And the way
these stay hidden is uniform: none of them crash. Four of the eight produced a confident,
plausible, working system that was wrong in one direction only.

**Found by:** a user's pushback, then a full-text sweep for every workbook-specific word — not by
any test. Two of them were caught by *generating* something and reading the output.

---

## Empty inserts crashed ingest — found by the first synthetic file, before it tested anything

`ingest_spec._write` ended with three `con.executemany(...)` calls. DuckDB's `executemany`
**raises** on an empty parameter list (`"requires a non-empty list of parameter sets"`). The real
PPAC and Budget workbooks always have footnote/source rows, so `notes` was never empty and the path
never ran. The very first synthetic test file — a clean fold with no footnote — hit it and crashed
ingest outright.

Fixed by guarding each insert with `if rows / if receipts / if notes`. A plain data export with no
title, unit or source line is a completely ordinary file, and it could not be ingested.

**Found by:** `tests/eval_ingest.py` on its first run — the round-trip harness reached a real bug in
shipped ingest before it got as far as checking a single value. Exactly the argument for evals-first:
a test file that is *not* the two hand-picked workbooks exercises paths the real ones hide.

---

## A wrong reading rated itself `high` on every field — the confirmation screen would flag nothing

The round-trip scoreboard on `keep_record` (a tidy record table that must NOT be melted) reproduced
the climbing-file bug in miniature, with an answer key: the 6-row record table was melted into 18
rows of `(symbol, measure_name, number)`, destroying the record. And the spec that did it carried
**four fields marked `high`** — `value_columns, data_rows, header_row, label_column`.

That is the block-2 lesson made mechanical. `propose.py` derives confidence from *"did a heuristic
return something"* (`HIGH if hi - lo >= 2`, `HIGH if lab`), not from *"do the independent checks
agree"*. So on a file it reads 100% wrong, every field is `high`, and the confirmation screen —
which only highlights sub-`high` fields (D59/D61) — highlights nothing. A confident wrong reading is
worse than an unsure one, because only the unsure one gets looked at.

**Found by:** `tests/eval_ingest.py` printing the `high` field list beside a failing row-set. The
red line `MELTED into 18 rows ... 4 'high' on a wrong reading` is the specification for option 1:
confidence must come from agreement between the header-pattern test and the direction-of-sameness
test, not from a heuristic returning non-None.

---

## The one-bit header test misfired twice — once too eager, once not eager enough

Building the layout classifier, the same one-line test was wrong in both directions on the first try.

1. **`looks_periodic([l])` per single label always said "not periodic".** That helper returns False
   for fewer than three labels (it needs a run to judge), so calling it once per header cell made
   *every* header non-periodic, and PPAC — 18 clean year columns — classified as a **record**. Fixed
   by matching each header against the period patterns directly. Caught by printing the verdict on
   the two real files before touching ingest.
2. **A naive "not periodic → record" flipped the Union Budget.** Its value headers are merged,
   multi-line, Hindi+English with the year in a row above, so they match no period pattern — and a
   record verdict would have stopped a working crosstab from unpivoting. Fixed by requiring a record
   verdict to have *clean* headers; messy headers stay crosstab at LOW confidence and are flagged for
   confirmation. Caught by running the classifier on the Budget before trusting it — not by a test.

**Lesson.** A one-bit classifier meets messy reality immediately, and the dangerous error is the
confident wrong flip (block-2's own warning). The fix was not a better guess but a smaller confident
region: be `high` only on the clean extremes, and hand everything ambiguous to the human. Found by
verifying the verdict on all three real files before wiring it downstream, exactly because the
synthetic eval could not have contained the Budget's header.

---

## The "distinctive token" was not distinctive enough — the ask-which-measure gate over-fired

The measure-gap gate decides whether the user named a measure by matching its DISTINCTIVE tokens.
First cut computed distinctive = a measure's tokens minus the tokens common to ALL measures. But
`dma` is shared by `Price vs 50-DMA` and `Price vs 200-DMA` and absent from `% vs pivot`, so it
survived the all-intersection and counted as naming BOTH DMA measures. "Morepen vs 200-DMA" then
matched two measures, read as ambiguous, and clarified a question it could answer — the over-refusal
failure again, where a too-eager gate refuses a real answer.

Fixed by defining distinctive as *belongs to exactly one measure* (owner count == 1), which drops
`dma` from both and keeps `200`/`50`. Caught immediately by the two-sided record-QA cases: the
"names a measure → must answer" side went red the moment the gate was added.

**Found by:** `tests/eval_record_qa.py` — the answered cases (Morepen, BLS) turning to `clarify` the
instant the gate landed. The reason two-sided suites exist: a gate is only half-tested by what it
should catch.

---

## A module named `pptx.py` imported itself

The first PPT renderer was `src/render/pptx.py`, and `from pptx import Presentation` resolved to the
module itself, not the installed `python-pptx` package — a partially-initialised circular import, and
the deck never got written. Renamed to `deck.py`. Small, but the lesson is a real one: never name a
module after the third-party package it depends on. Caught immediately by running the build and
reading the traceback rather than trusting that "it imported".

---

## The corpus caught what two hand-picked files could not

Two ingest bugs survived because PPAC and the climbing file never exercised the shape. A synthetic
corpus (`tests/eval_corpus.py`, round-trip on 8 layouts) surfaced both on its first run.

1. **A single-measure record was read as a cross-tab.** A two-column list (`City | Population`) has
   one value column, and the record rule demanded ≥2, so it fell through to crosstab and melted.
   Fixed: a single *non-periodic* value column is still a record (medium confidence → confirmed).
2. **A single-row time series found no table.** `value_columns` required ≥3 numbers per column; a
   one-row sheet has one, so it saw nothing. Fixed with a guarded fallback — a *contiguous* run of
   ≥3 numeric columns counts (contiguity stops a stray number in a title row from qualifying).

**Found by:** `tests/eval_corpus.py`, first run. The lesson from D58 again: the dangerous gap is the
shape your two demo files happen not to have.

---

## The exploration prototypes broke in exactly the shapes they were meant to test

Building the A/B lineage prototypes (D79–D83), each failed in its characteristic way — which is the
tradeoff data, not noise.

1. **Approach A dropped a filter.** DeepSeek put `product=HSD` in both the from- and to-period
   selectors; my compiler only read the *differing* key (year), so the filter vanished and the
   number was all-fuel, not diesel. Fixed by moving keys common to both periods into the base filter.
2. **Approach A over-cited.** The first lineage query grabbed *all* rows for a state (every year,
   every product) → 1,886 cells for a 72-cell answer. Fixed to the two periods actually used.
3. **Approach B's gate over-rejected.** The column allowlist flagged `growth_rate`/`value_2019` as
   unknown — but those are *derived aliases*, not base columns. Validating LLM SQL needs alias-aware
   scoping, not a naive column walk.
4. **Approach B's lineage returned nothing.** Naive predicate extraction turned `year IN ('a','b')`
   into contradictory `year='a' AND year='b'` → zero rows. Fixed by reusing the query's *own* WHERE
   text verbatim.

**Found by:** running each prototype on the one deep question and reading the actual output — not by
trusting that it "worked."

---

## sqlglot API drift, and a DAG built in disconnected pieces

Two bugs in the SQL→operator-DAG parser (D83), both giving *zero* provenance until fixed.

1. **`from_`, not `from`.** sqlglot 30.x renames the FROM arg off the Python keyword. My earlier
   "From-traversal quirk" was this wrong key all along — `sel.args.get("from")` was always None, so
   no scan predicate was ever captured.
2. **CTEs built as separate subtrees.** The main query and each CTE were converted independently with
   no edge between a `FROM cte` reference and that CTE's node, so the provenance walk never reached
   the scans that hold the predicates → 0 cells. Fixed by wiring CTE references as edges.

**Found by:** verifying provenance *values* (expected 72 cells, got 0) rather than that the parser
"ran". Asserting the number, not the status — the D38 rule, one more time.

---

## Integration collateral: a delete that deleted the demo, and a test that crashed on a type

Wiring the escape hatch into the product (D84) surfaced two things that were not the feature itself.

1. **The delete button had removed the climbing workbook.** During earlier browser testing the
   delete feature (spec + db + *source file*) was used on the climbing file, so its `.xlsx` was gone
   and `eval_record_qa` couldn't load it. Restored by reconstructing the xlsx from a CSV export I had
   saved. Lesson: a delete that removes the user's source data is working as designed — and that is
   exactly why it needs its inline confirm (D76), which it has.
2. **`eval_e2e` crashed on `float('BIHAR')`.** DeepSeek *occasionally* mis-plans the Bihar-share
   question as a group-by-state QueryPlan (`v1='BIHAR'`), and the value check called `float()` on it.
   Hardened the harness to treat a string-where-number as a mismatch, not a crash. The mis-plan is
   pre-existing planner non-determinism, orthogonal to the escape hatch (confirmed: the failing answer
   was a typed plan, not an escape answer).

**Found by:** running the real suites after the change — the crash and the missing spec both showed up
immediately, not in production.

---

## The planner mapped "median" to "avg" — a wrong answer that looked right

Asked for the median, the typed planner (with a capable model) silently answered the *average* — the
grammar has no median, so it reached for the nearest thing. A wrong number with a clean citation, the
worst kind (§1). Fixed structurally: median/stddev/variance/percentile/correlation are declared beyond
the grammar and route to the SQL escape hatch, which computes the true value (verified: 1,109.66 vs an
independent DuckDB `median()`).

**Found by:** eyeballing an answer that read "average" for a "median" question during the browser
verification. Domain-and-attention, not a test — the same way the very first wrong-aggregation bug was
caught.

## A pre-push review found a silent wrong-answer, a leaked unit, and a claim that fought itself

Four things surfaced when the whole thing was audited end-to-end before pushing, and they sort into
the same lesson the project keeps relearning: **the dangerous bugs are the ones that still look
right.**

- **Breakdowns answered with one arbitrary group (real wrong answer).** A "by year" question
  compiled to a legal grouped SQL returning 18 rows; `execute` called `.fetchone()` and voiced the
  first — "In 2008-09, Bihar used 185.40" — status `answered`, every gate green. The gate suite
  never saw it because no gate inspects result *cardinality*. Fixed by detecting the multi-row
  breakdown and asking which group (D87).
- **A PPAC unit leaked onto every file (wrong, clean-looking label).** `UNITS = {"value": "thousand
  metric tonnes"}` was a hardcode that should have moved to the spec; a ₹-crore Union Budget total
  came back labelled in metric tonnes. Now sourced from `catalog.unit` per spec (D88).
- **A doc claim that contradicted itself.** The README said "$0.0002 per full 15-question run"; that
  $0.00022 figure is a **6-question** eval (4 of 6 never hit the API), and the same README says
  ~$0.0015 for 15 questions two other places. "Where did this number come from?" would have landed
  in the interview. Reworded to name the 6-question denominator and reconcile with the 15-q figure.
- **Two hygiene artifacts:** `_provenance` was pasted twice verbatim in `execute.py` (second shadowed
  the first); `tests/eval_planner.py` was an orphan importing a removed symbol and reading
  `questions.yaml` fields that no longer exist — deleted (D89).

**Found by:** a two-agent pre-push audit — one agent re-ran every checkable README claim against the
live code and databases, one read every `src/` module against `DECISIONS.md`. The breakdown bug and
the unit leak were both **reproduced by running the code**, not inferred from the names. Attention
plus execution, the same combination that caught the median and wrong-aggregation bugs — not a test
that was already written.

## The confirm endpoint trusted the browser — three ways into the same wound

`/api/confirm` takes a spec the browser sends and builds a database from it. Three fields on that
spec reached SQL or the filesystem without being checked, and all three were the same mistake:
**a value that should be inert data was used as code.**

- **`spec.table` → arbitrary SQL.** It was pasted *unquoted* into `CREATE TABLE {spec.table}`, and
  DuckDB runs `;`-separated statements, so `x (i INT); DROP TABLE cell_map; --` dropped the
  citations table. Reproduced on a throwaway db (3 rows → gone) and through the live endpoint.
- **`spec.file` → path traversal.** The API joins `DATA / spec.file`; one branch (`/api/sheet`)
  forgot to basename, so `../../x.xlsx` read outside `data/`.
- **column names → quoted-identifier break-out.** Names reach `CREATE TABLE` double-quoted; an
  embedded `"` escapes the quotes. They come from spec fields (crosstab) *and* raw workbook header
  cells (record layout), which the label cleaner never stripped.

The fix was the same shape each time and it is the project's whole thesis (D9): **don't ask the
code to remember to sanitise — make the bad value impossible to construct.** `table` and `file` got
`field_validator`s on the `Spec` model, so neither the API nor the CLI can build an injectable spec.
Column names got one guard at `_write`, the single point where every name — spec-derived and
workbook-derived — meets the CREATE.

The one wrong turn, and the lesson inside it: the first instinct was to validate *every* identifier
as a plain `[A-Za-z_]` name. Running it against the real files killed that in one line — the climbing
file's measure is literally `% vs pivot (now)`, a legal double-quoted column. **The over-strict fix
would have rejected a working file.** Only `table` is unquoted, so only `table` gets the strict rule;
quoted names get the narrower "no embedded quote" rule. Overfitting the *fix* to the *attack* is its
own bug.

**Found by:** a pre-push architecture review that read every `src/` module against the design log,
then *reproduced each vector by running it* — the DROP on a scratch db, the traversal and the 400
through a `TestClient`. Not pattern-matching on names; executing the attack and watching it fail
after the fix. (D90, D91.)

## The story arranger never ran — a one-line tuple assignment that always threw

`deck_agent.story()` is meant to let the model choose the ORDER of already-cited findings. It never
did. The dedup line was written as one tuple assignment:

```python
seen, order = set(), [i for i in order if not (i in seen or seen.add(i))]
```

Python evaluates the whole right-hand side before binding the left, so when the comprehension runs,
`seen` is not defined yet — `NameError`, every time the model returned a plan. The `except: return
default` right below swallowed it, so the deck silently fell back to the mined order and *looked*
fine. A feature that was never exercised, hidden by a bare except.

Split into two statements (`seen = set()` then the comprehension) and confirmed the model's chosen
order now survives (a fake planner returning `order=[1,0]` produces `[1,0]`).

**Found by:** rewiring the deck to use the caller's planner (D92) — a fake planner that *always*
returns a plan drove the previously-dead success path for the first time, and it threw on the first
call. The bug had been invisible because reaching it required a local model to be up AND returning
valid JSON, and even then the bare except buried it. Lesson, again: a bare `except` that returns a
default converts a crash into a silent wrong behaviour — the deck was "working" by never working.
