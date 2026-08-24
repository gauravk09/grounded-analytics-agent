"""Build a demo deck from REAL Answer objects — deterministic, no model, no API cost.

Spans both workbooks and all three outcomes, because the deck has to show the refusals too: the
system's value is as much what it declines as what it answers.
"""
from pathlib import Path
import sys
root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root / "src"))

from spec import Spec
from catalog import build
from plan import QueryPlan, Metric, Filter
from execute import execute
from answer import Answer, compose_refusal
from render.deck import build_deck


def q(col, agg="sum", filters=(), group_by=(), sort="none", limit=0, narr="{v1}"):
    return QueryPlan(kind="query", metric=Metric(agg=agg, column=col),
                     filters=[Filter(column=c, op="=", value=v) for c, v in filters],
                     group_by=list(group_by), sort=sort, limit=limit, narration=narr)


answers = []

# ── PPAC (crosstab) ───────────────────────────────────────────────────────────
pp_spec = Spec.load(root / "specs" / "ppac.yaml")
pp_db = root / "data" / "ppac_statewise_sales.duckdb"
pp = build(pp_db, pp_spec.table, spec=pp_spec)
answers.append(execute(
    q("value", filters=[("state", "GUJARAT"), ("product", "HSD"), ("year", "2019-20")],
      narr="Gujarat consumed {v1} thousand tonnes of diesel in 2019-20."),
    pp, pp_db, question="How much diesel did Gujarat consume in 2019-20?"))
answers.append(Answer(question="How much tax revenue did that generate?", status="abstained",
                      abstain_reason=compose_refusal("no_such_column", "tax", pp)))

# ── Climbing (record) ─────────────────────────────────────────────────────────
cl_spec = Spec.load(root / "specs" / "BananaPatterns-Climbing-2026-08-18.yaml")
cl_db = root / "data" / "BananaPatterns-Climbing-2026-08-18.duckdb"
cl = build(cl_db, cl_spec.table, spec=cl_spec)
answers.append(execute(
    q("% vs pivot (now)", group_by=["Name"], sort="desc", limit=1,
      narr="{v1} has the highest % vs pivot, at {v2}."),
    cl, cl_db, question="Which stock has the highest % vs pivot?"))
answers.append(execute(
    q("% vs pivot (now)", group_by=["Name"], sort="desc", limit=1, narr="{v1} {v2}"),
    cl, cl_db, question="Which stock is highest?"))          # -> clarify (unnamed measure)

out = build_deck(answers, root / "output" / "demo_deck.pptx",
                 title="Data-analysis assistant",
                 subtitle="Every number traces to a source cell · it refuses when it cannot")
print(f"wrote {out}  ({len(answers)} answer slides + title)")
