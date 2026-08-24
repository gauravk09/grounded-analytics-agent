"""Askability scoreboard for a RECORD workbook (the climbing file). Deterministic — no model.

We hand-build the plans a good planner would emit, run the real compile+execute+lineage, and assert
the ANSWER VALUE and that its CITATION cell, read back from the ORIGINAL xlsx, holds that value.
Asserting the number (not just status) is D38; reading the source cell (not our own DB) is the
verify-against-source rule. This guards the record path the way eval_e2e guards the crosstab path.
"""
import sys, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))

import openpyxl
from spec import Spec
from catalog import build
from plan import QueryPlan, Metric, Filter
from execute import execute

XLSX = root / "data" / "BananaPatterns-Climbing-2026-08-18.xlsx"
DB = root / "data" / "BananaPatterns-Climbing-2026-08-18.duckdb"
spec = Spec.load(root / "specs" / "BananaPatterns-Climbing-2026-08-18.yaml")
cat = build(DB, spec.table, spec=spec)
ws = openpyxl.load_workbook(XLSX, data_only=True)["Climbing"]


def source_cell(a1: str):
    return ws[a1].value


def q_metric(col, agg="sum", filters=(), group_by=(), sort="none", limit=0, narration="{v1}"):
    return QueryPlan(kind="query", metric=Metric(agg=agg, column=col),
                     filters=[Filter(column=c, op="=", value=v) for c, v in filters],
                     group_by=list(group_by), sort=sort, limit=limit, narration=narration)


CASES = [
    # (label, plan, expected_value, expected_cell)
    ("highest % vs pivot",
     q_metric("% vs pivot (now)", group_by=["Name"], sort="desc", limit=1,
              narration="{v1} highest at {v2}"), 59.0, "F2"),
    ("Morepen vs 200-DMA",
     q_metric("Price vs 200-DMA", filters=[("Name", "Morepen Laboratories Limited")],
              narration="{v1}"), 90.9, "H3"),
    ("BLS vs 50-DMA",
     q_metric("Price vs 50-DMA", filters=[("Name", "BLS E-Services Limited")],
              narration="{v1}"), 15.6, "G4"),
]

# Two-sided measure-gap: same plan, but a question that names no measure must CLARIFY, not answer.
AMBIG = q_metric("% vs pivot (now)", group_by=["Name"], sort="desc", limit=1, narration="{v1} {v2}")

passed = 0
_amb = execute(AMBIG, cat, DB, question="which stock is highest?")
_ok = _amb.status == "clarify" and "measure" in _amb.text().lower()
print(f"  {'PASS' if _ok else 'FAIL'}  unnamed measure -> clarify: {_amb.text()[:60]}")
passed += _ok

for label, plan, want_val, want_cell in CASES:
    a = execute(plan, cat, DB, question=label)
    fails = []
    if a.status != "answered":
        fails.append(f"status={a.status}")
    else:
        slot = a.slots.get("v2") if plan.limit == 1 else a.slots.get("v1")
        got = float(slot.raw) if slot else None
        if got is None or abs(got - want_val) > 0.01:
            fails.append(f"value={got} want {want_val}")
        cells = [str(c) for c in a.all_citations()]
        if not any(want_cell in c for c in cells):
            fails.append(f"cell {want_cell} not in {cells}")
        # source-of-truth: the cited cell in the ORIGINAL file must hold the value
        if abs((source_cell(want_cell) or -1) - want_val) > 0.01:
            fails.append(f"source {want_cell}={source_cell(want_cell)} != {want_val}")
    print(f"  {'PASS' if not fails else 'FAIL'}  {label}: {a.text()[:70]}")
    for f in fails:
        print("      " + f)
    passed += not fails

print(f"\n{passed}/{len(CASES)+1} record-QA cases passed")
sys.exit(0 if passed == len(CASES) + 1 else 1)
