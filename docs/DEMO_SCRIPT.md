# Demo recording script — Grounded Analytics Agent

~2 minutes. Records the project's three real strengths: **every number traces to a source cell**,
**derived values keep their lineage**, and **it refuses instead of guessing**. Record with
**Cmd+Shift+5** (macOS → Record Selected Portion, drag over the browser) or QuickTime.

## Pre-flight (once, before recording)

1. Servers up: frontend `http://localhost:5173`, backend `http://localhost:8000` (both running;
   the backend was just restarted so it runs the latest code).
2. Open `http://localhost:5173`, **zoom 100%** (Cmd+0), browser as **wide as possible** (the answer
   and the Evidence/SQL panel sit side by side).
3. Type your **DeepSeek key** into the field at the top-right, or record yourself typing it in
   Scene 2. Leave **"local" unchecked**.
4. Upload files are ready in **`~/Desktop/GAA-demo-uploads/`** — you'll upload
   `ppac_statewise_sales.xlsx` in Scene 3.

## Scene 1 — Start from scratch (~12s)

- Start recording. The sidebar shows three example workbooks already loaded.
- Say: *"It ships with a few example workbooks. Let's clear them and start from nothing."*
- Hover each source, click the **trash**, then **Delete** — remove all three. Sidebar is now empty
  ("Nothing yet. Add a spreadsheet to begin.").

## Scene 2 — Add the API key (~8s)

- Click the **API key field** (top-right, placeholder `DEEPSEEK_API_KEY`) and paste your key.
- Say: *"My key lives in memory only — never logged, never written to the repo."*

## Scene 3 — Upload a spreadsheet (~25s)

- Click **+ Add a spreadsheet** → **Choose a file…** → pick
  `~/Desktop/GAA-demo-uploads/ppac_statewise_sales.xlsx`.
- On **"Here's what I found"**: point out it worked out the header row, data rows, and value columns
  **by counting — no model**.
- On **"A few things the file doesn't say"**: the model inferred **state / year / sales**. Say:
  *"Layout it measures; meaning it asks — and only what the file can't tell it."*
- Click **That's right — add it** → **"Added. 2,210 rows · 12,882 source-cell receipts."**

## Scene 4 — A cited answer, verified against the sheet (~25s) ← the core

- Ask: **`Which state was highest in 2024-25?`**
- Answer: **"GUJARAT was highest in 2024-25, at 26,130.53 thousand metric tonnes."**
- Point at the **Evidence** panel: two source cells (`A42`, `R42`), "Computed as…", and the real
  **SQL**.
- **Click the `R42` chip** → the **sheet opens** with that exact cell highlighted. Say: *"Click any
  number and it opens the source cell — the answer is welded to the sheet, not generated."* Close it.

## Scene 5 — A derived value that keeps its lineage (~15s)

- Ask: **`What share of the total was Gujarat in 2024-25?`**
- Answer: **"Gujarat accounted for 12.13% of the total in 2024-25."**
- Point at the Evidence: the percentage is a **tree** — numerator and denominator, each with their
  own cells. Say: *"Even a computed number traces down to the cells that fed it — we do the
  arithmetic, never the model."*

## Scene 6 — It refuses instead of guessing (~15s) ← the differentiator

- Ask: **`How much tax was collected in 2024-25?`**
- It **abstains**: *"I can't answer this from the file — there is no tax column in this file."*
- Say: *"When the file can't answer, it says so — a blue 'abstained', not a confident wrong number.
  That refusal is composed from the catalog; the model never gets to invent one."*

## Scene 7 — Turn it into a deck (~20s)

- Click **✦ Presentation** (in the ask bar). Wait ~10–20s.
- Show **"✓ Presentation ready — check your downloads."** and the `.pptx`.
- Say: *"One click turns the cited findings into a designed deck — every figure on every slide still
  traces back to a cell."*
- Stop recording.

## Notes

- **Upload → ask now works for any unit.** A file whose unit contains digits (e.g. "000 Metric
  Tonnes") used to 500 on value questions; the running backend now whitelists digits that come from
  the unit, so it's safe. (This was a stale-backend issue during earlier testing — fixed by the
  restart.)
- If a **share** question ever abstains, just re-ask — the planner is mildly non-deterministic on
  two-part (derived) plans. On the current backend the three questions above are reliable.
- Deleting a source also deletes its file from `data/` — that's why the uploads live in
  `~/Desktop/GAA-demo-uploads/`, safe from the on-camera delete.
