# ROADMAP

The journey: what we explored, what we rejected, what we learned, what's next.
Finalised decisions live in [DECISIONS.md](DECISIONS.md).
Every bug found while building, and what it changed, lives in [BUGS.md](BUGS.md).

---

## The assignment, stripped down

Build a data-analysis assistant over a CSV/XLSX with two hard constraints:

1. **Data lineage** — every number traceable to where it came from in the source file
2. **Zero hallucination** — when it can't back a claim with data, it abstains

Plus: PPT/PDF generation must be a natural next step, not a rewrite.

**How they grade it:** it runs · lineage holds up under interrogation · it refuses cleanly ·
the PPT/PDF path is believable · judgment (why this dataset, why this approach, what you'd do
with another week).

Their own words: *"one slice that works end-to-end and holds up to scrutiny"* beats
*"a broad system with hand-wavy guarantees."* That is explicit permission to build narrow and deep.

---

## Progress

| # | Lesson | Status | Builds |
|---|---|---|---|
| 0 | The problem | ✅ | — |
| 0.5 | Why refusal is hard | ✅ | — |
| 1 | Choosing the dataset | ✅ | `data/ppac_statewise_sales.xlsx` |
| 2 | Ingest & the receipt book | ✅ | `src/ingest.py` |
| 3 | The catalog | ✅ | `src/catalog.py` |
| 4 | The planner | ✅ | `src/plan.py`, `src/planner.py` |
| 5 | Compile, execute, prove | ✅ | `src/compile.py`, `src/execute.py` |
| 6 | The refusal gates | ✅ | `src/verify.py`, `src/ask.py` |
| 7 | The Answer object | ✅ | `src/answer.py`, `src/trace.py` |
| 8 | Streamlit | ✅ | `src/render/streamlit_app.py` |
| G1 | Generic ingest: layout classifier + record path | ✅ | `propose.classify_layout`, `ingest_spec._ingest_record`, `tests/eval_ingest.py` |
| G2 | Record workbooks askable (form A + 3 fixes; form B rejected) | ✅ | `catalog`, `propose`, `present.suggestions`, `planner`, `tests/eval_record_qa.py` |
| G3 | Stacked-header combine (Budget) + ask-which-measure | ✅ | `propose`, `ingest_spec`, `verify.find_measure_gap` |
| G4 | CSV/TSV upload · richer findings (trend/top-N/share) · PPT in-app button · AI-native UI | ✅ | `xlsx_io`, `deck_agent`, `deck`, `api /api/deck`, `frontend/*` |
| G5 | Ingestion corpus (8/8 shapes) · click-cell-to-open-sheet · delete source · brief-driven decks | ✅ | `tests/eval_corpus.py`, `SheetViewer`, `api /api/sheet`, brief popover |
| 9 | PPT path: designed deck + agentic authoring | ✅ | `src/render/deck.py`, `deck_agent.py`, `make_deck.py` |
| 10 | Interview prep | ⬜ | `README.md` |

**Working end to end.** `python src/ask.py "<question>"` or
`.venv/bin/streamlit run src/render/streamlit_app.py`.

- ingest → 2,210 rows, 12,882 cell receipts, 352 formulas, 18 citable notes
- `tests/eval_e2e.py` → **9/9**, asserting status, exact citations *and* the numbers
- cost → **$0.0008** per full 9-question run; refusals cost nothing at all
- four answer paths: answered · abstained · clarify · unsupported

**Verified against the source workbook, not against our own database:** Gujarat HSD 2019-20 →
`HSD!M42` = 5607.584527746732 · UP MS 2024-25 → `MS!R18` = 4832.791 · Uttarakhand HSD 2019-20 →
`HSD!M19` = 744.0101747008262 · Bihar MS 2024-25 → `MS!R33` = 1161.232 · NORTH HSD 2024-25 → the
ten cells `HSD!R10`–`R19` summing to exactly 27,551.84.

---

## The mental model everything hangs on

An LLM is an intern who is brilliant at English, genuinely bad at arithmetic, and physically
incapable of saying "I don't know."

| Job | Who | Rule |
|---|---|---|
| 🧑 Intern | the LLM | reads English, decides *what to look up*. Never emits a digit. |
| 🧮 Calculator | DuckDB | does the math. Deterministic. |
| 🧾 Receipt book | `cell_map` | written at file-open time. Maps every value → `Sheet!A1`. |

---

## What we learned along the way

**1. The two constraints forbid the obvious build.**
"Every number traceable" + "must abstain" together mean the LLM is not allowed to produce numbers
*at all* — not even correct ones. That inversion determines the whole architecture.

**2. Lineage dies in one line.**
`pd.read_excel()` and every cell address is gone. It cannot be recovered afterwards, because
blank-row dropping, merged cells, filtering and sorting each shift the mapping differently and the
errors compound. Lineage is a fact about *how the file was read*, not a property of the data.

**3. Wrong answers look exactly like right answers.**
This is why refusal can't live in an `except` block. Ask for churn over a sheet with no churn
column and a naive system finds `retention_pct`, computes something, and returns `87.3%` with no
error. Green pipeline, fictional number.

**4. The catalog turns an impossible question into a lookup.**
"Is this answerable?" is a judgment call. "Is `churn` in this list of columns?" is set membership.
Giving the model a fixed catalog converts one into the other.

**5. There are four kinds of "can't answer", detected at different stages.**
Missing concept (before the query) · ambiguous entity (before) · empty result (**after** — the
query is valid and returns zero rows) · not a data question (before). So refusal has to be layered
along the pipeline, not a single gate at the front.

**6. Over-refusal is a real failure mode.**
A system that refuses everything is trivially safe and useless. This is why the demo needs
3 answerable questions *and* 2 refusals — the answers prove the line is in the right place.

**7. Domain knowledge is a debugging tool.**
Picking an Indian dataset means a wrong Maharashtra figure sets off an alarm. A wrong Ohio figure
would not.

**8. Fix traps in the compiler, not the prompt.**
The `row_kind` filter is injected by our code, not requested of the model. Anything you ask a
model to remember, it will eventually forget.

**9. Auto-detecting spreadsheet structure is the *more* dangerous choice, not the safer one.**
Guessing the header row wrong makes every number and every citation wrong — confidently. That's
hallucination relocated into the ingest layer, where nobody is looking for it. (See D16.)

---

## Discussions & pushbacks

Points Gaurav pushed back on that changed the design. Both have the same shape, and both are
worth being able to tell in the interview.

### 1. "This is hardcoded to one dataset — how is it universal?"

`ingest.py` had `HEADER_ROW = 8` typed into it. Fair hit.

The resolution was not "auto-detect everything" — silently guessing the header row makes every
number *and* every citation wrong, confidently, which is hallucination relocated into the ingest
layer. It was: **separate the reading (universal, mechanical) from the interpretation (per-file,
must be confirmed)**, then heuristics → LLM proposes layout → human confirms once → freeze to a
spec file. See D16.

### 2. "Nobody is going to hand-write an alias list at runtime"

Same hit, one layer up. D18 assumed a curated semantic layer, which is right for this file and
wrong for a product.

The resolution introduced the piece the design was missing: **asking**. Matching is three-way —
map when there is one confident candidate, **ask** when there are several, abstain when there are
none. Confirmations are remembered, so the alias list is learned rather than authored. See D20.

**The general lesson from both:** the first instinct is a binary — guess or refuse. The right
answer usually has a third option in the middle, and it is almost always *ask the human, once,
about the specific thing in doubt*. Refusing when one question would resolve it is over-refusal.

---

## What we rejected, and why

### Architecture
| Rejected | Why |
|---|---|
| RAG over spreadsheet chunks | Model does the arithmetic (unverifiable); lineage degrades to "here are the chunks" |
| LLM writes pandas, we `exec` it | Arbitrary Python can't be statically validated; can't recover contributing rows |
| Model writes raw SQL | Viable with a frontier model; too fragile with `llama3.2:3b` |
| Validate the model's numbers after generation | A filter, not a guarantee. Slots make bad numbers unrepresentable instead |
| Refusal as an `except` branch | Fires when code breaks, silent when data is absent — exactly backwards |

### Ingest
| Rejected | Why |
|---|---|
| `pd.read_excel` then map back to cells | Mapping is unrecoverable once pandas has reshaped |
| Offset arithmetic (`sheet_row = index + 9`) | Blank rows, merges, filters and sorts each break it differently |
| Dropping `Region Total` / `ALL INDIA TOTAL` | Deleting data is a lie; `ALL INDIA TOTAL` is a real published figure |
| Fuzzy/clever row classification | Silent misfire corrupts everything downstream. Exact matching breaks *loudly* instead |
| Auto-detecting layout silently | See learning #9 |

### Datasets considered
| Candidate | Verdict |
|---|---|
| **PPAC POL consumption** | ✅ **chosen** — formulas + mess + aggregate trap + confusable states + natural refusal |
| EIA fuel taxes (US) | Equally strong on every axis, but US domain — no gut-check advantage |
| Union Budget at a Glance | Best mess (bilingual, ₹ crore, data starting at column D, `..` as missing) and most familiar — but **zero formulas** |
| Annual Financial Statement | ~817 merged ranges, zero-padded head codes — but `Sheet1..Sheet9` and no formulas |
| Economic Survey chart data | 20 sheets of independent chart panels, no aggregate trap, 1.3 MB |
| Superstore / Global Superstore | Outstanding name-ambiguity (174 "Xerox" products), but clean — ingest would look trivial |
| Census MARTS (US) | True 3-way join and a brutal 3-row header, but no formulas |
| ONS Business Demography | Good, but 40+ sheets is more navigation than a demo wants |

Could not verify: RBI publications (ASP.NET postback, no direct links), MoSPI (React SPA shell),
data.gov.in (API-key flow).

---

## The demo: 5 questions

3 answerable + 2 refusals. Both halves are load-bearing.

1. **Cross-sheet** — petrol vs diesel share for a state (joins `MS` and `HSD`)
2. **Year-over-year** — which states grew fastest between two years
3. **Formula-cell citation** — a number whose source cell is `=SUM(...)` or the external `VLOOKUP`
4. **Ambiguity → refuse** — "Andhra" matches `ANDHRA PRADESH` and `ARUNACHAL PRADESH`
5. **Missing concept → refuse** — "how much tax revenue did that generate?" (volumes only)

**Bonus demo, and probably the best moment:** *"why don't the region totals add up to ALL INDIA
TOTAL?"* → the system points at cell `A57`: *"Total Fig. includes IMPORTS & SEZ STATE"*.
Measured gap: 241,612 − 223,480 = **18,132** in FY2025-26.

---

## Open questions / parked

| Item | Status |
|---|---|
| **Spec refactor** — pull ingest constants into `specs/ppac.yaml` (D16) | Parked, ~1 hour. Converts the generality answer from a claim to a demo |
| **coreworks.ai signup**, ~15 min | ⏸️ **Gaurav's** — blocks the README impressions paragraph |
| Confirm the real deadline | The brief says "Monday, 1st June" — check with them |
| Telangana bifurcation (2014) | A real data trap. Candidate for "what I'd do with another week" |
| `VLOOKUP` to an external workbook (`[1]POL!`) | FY2025-26 values come from a file we don't have. Honest answer: say so. Good lineage story |
| Scaling the receipt book | Currently ~5 receipt rows per data row. At a million rows, store the rule not the rows. Likely interview question |
| **Re-upload / file versioning** | What happens when the same dataset is uploaded again later, or PPAC republishes with a new year column? Open questions: does the warehouse get rebuilt or versioned? Do old citations still resolve if row ids shift? Should a citation record *which version* of the file it points at? Currently ingest just drops and recreates the tables — fine for a demo, wrong for a product. |
| **Summary statistics / data profiling** | Mean, median, mode, distributions — deliberately kept **out** of the catalog (see D17), but genuinely useful. Right shape: a "profile this dataset" mode that emits pre-computed Answer objects with full citations, reusing the same pipeline and renderers. Good "another week" item. |

---

## What I'd do with another week

- The spec refactor plus an LLM structure-proposer with human confirmation (D16)
- Entity-history awareness: Telangana/Andhra 2014, J&K/Ladakh 2019 — a state-lineage table so
  year-over-year comparisons across a bifurcation refuse rather than mislead
- Follow the external `VLOOKUP` to the source workbook so FY2025-26 is fully traceable
- Charts in the Answer object, so the PPT renderer gets visuals for free

---

## Ship-readiness: what's done and what's known (current, as of D85)

The list above is the original journey; this is the state now, for a reviewer deciding to ship.

### Landed since the original plan (D66–D85)
- **Generic ingest** — layout classifier (cross-tab vs record), record path, stacked-header combine,
  CSV/TSV, single-measure & single-row shapes. Proven by `tests/eval_corpus.py` (**8/8**).
- **Q&A** — cited answers, refusals, ask-which-measure, click-a-reference-to-open-the-sheet, and
  **contiguous cells collapsed into ranges** with whole-block highlight.
- **Deep questions** — the SQL **escape hatch** behind the validated-plan seam: frontier model writes
  SQL → sqlglot safety gate + read-only → operator-DAG lineage → answer only if traceable, else abstain.
- **Presentations** — designed decks, trend/bar charts, story-from-data, brief-driven, in-app toggle.
- **App** — AI-native React UI, workbook delete, inline onboarding.

### Test status (run before ship)
`tests/eval_gates.py` **69/69** · `tests/eval_ingest.py` **3/3** · `tests/eval_corpus.py` **8/8** ·
`tests/eval_record_qa.py` **4/4** · `tests/eval_e2e.py` **~14–15/15** (one flaky, see below) · `tsc` clean.

### Known issues & limits (honest, before ship)
| # | Issue | Severity | Note |
|---|---|---|---|
| 1 | **Escape hatch needs a capable model** | expected | No DeepSeek key / weak local model → deep questions abstain (never wrong). Browser needs the key entered. |
| 2 | **Rank/max lineage over-approximates** | low | Cites all rows the query scanned, not just the winner — a safe superset, never a miss. |
| 3 | **SQL→DAG: single base table only** | medium | Cross-table joins need the × rule; window functions → DAG opaque → the hatch abstains. |
| 4 | **Planner non-determinism** | medium | DeepSeek occasionally mis-plans (Bihar share → group-by-state). No retry / self-consistency yet. Causes the 1 flaky e2e. |
| 5 | **SQL path is scalar-only** | medium | "List the top 5 …" via the escape hatch is deferred; typed path still handles the common list/rank. |
| 6 | **Budget stacked headers not split** | low | Combined into unique periods (usable); not split into year × estimate-type dimensions. |
| 7 | **Onboarding label quirk** | cosmetic | A record file's geometry line reads "names in column E" (E is the last identifier, not the entity). |
| 8 | ~~Legacy Streamlit~~ **removed** | done | `streamlit_app.py`/`confirm_app.py` deleted, servers killed, deps/docs cleaned (D86). |
| 9 | **Delete removes source data** | by-design | Inline-confirmed (D76), but it deleted a demo file during testing (restored). Consider soft-delete. |
| 10 | **Guarded env-key hook** | note | `ALLOW_ENV_KEY=1` opt-in fallback added for local testing; **off by default** (D40 holds). Remove if unwanted. |
| 11 | **README impressions paragraph** | todo | Still owed — needs the coreworks.ai signup. |
| 12 | **e2e costs API + is slow** | note | 15 DeepSeek calls (~$0.0015, ~2 min). Fine for CI-on-demand, not per-commit. |

### The research arc (D79–D83), parked as prototypes — not shipped
`prototypes/` holds the A-vs-B tradeoff, the lineage stress test, the shared-node DAG, and the
SQL→operator-DAG parser. Only the parser + gate + path (`src/sql_dag.py`, `sql_gate.py`, `sql_path.py`)
were promoted into the product (D84). The rest is exploration, kept for the write-up.
