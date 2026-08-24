"""Approach A — a TYPED multi-step plan the LLM emits, my code compiles and executes with lineage.

The grammar grows by exactly one shape: group -> per-group derivation -> outer aggregate. That is
enough for "average growth across all states". The LLM fills a form; it never emits a number. The
compiler builds the CTE, whitelists every column, and validates every filter value against the
catalog's known labels (so no injection and no invented value). Lineage is a TREE: the outer
aggregate over per-group values, each of which cites its own cells — the same shape as today's
derived value, one level deeper.
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv()
import duckdb
from spec import Spec
from catalog import build, Catalog
from planner import make_planner, DeepSeekPlanner


class MultiStepPlan(BaseModel):
    group_by: str = Field(description="column to compute one value PER, e.g. state")
    measure: str = Field(description="numeric column to aggregate, e.g. value")
    op: Literal["percent_change", "difference", "ratio", "level"]
    from_filter: dict = Field(default_factory=dict, description='e.g. {"year":"2019-20"}')
    to_filter: dict = Field(default_factory=dict, description='e.g. {"year":"2024-25"}')
    base_filters: dict = Field(default_factory=dict, description='e.g. {"product":"ALL"}')
    outer_agg: Literal["avg", "sum", "min", "max", "count"]
    narration: str = Field(description="sentence with one hole {v1}, no digits")


PROMPT = """Emit a JSON plan for a question that needs a value computed PER GROUP and then
aggregated. Shape:
{"group_by": "<column>", "measure": "<numeric column>",
 "op": "percent_change | difference | ratio | level",
 "from_filter": {<col>:<value>}, "to_filter": {<col>:<value>},
 "base_filters": {<col>:<value>}, "outer_agg": "avg|sum|min|max|count",
 "narration": "sentence with one {v1} hole and no digits"}
Use ONLY the columns and values shown. For percent_change/difference/ratio, from_filter and
to_filter pick the two periods; for level, leave them empty. Return JSON only.

SCHEMA:
%s
QUESTION: %s"""


def _validate(plan: MultiStepPlan, cat: Catalog):
    cols = cat.column_names
    for c in [plan.group_by, plan.measure]:
        if c not in cols:
            raise ValueError(f"unknown column {c!r}")
    for f in (plan.from_filter, plan.to_filter, plan.base_filters):
        for k, v in f.items():
            if k not in cols:
                raise ValueError(f"unknown filter column {k!r}")
            labels = cat.labels_for(k)
            if labels and str(v) not in labels:
                raise ValueError(f"value {v!r} not a known label of {k!r}")


def compile_and_run(plan: MultiStepPlan, cat: Catalog, db: Path):
    _validate(plan, cat)
    con = duckdb.connect(str(db), read_only=True)
    m, g = plan.measure, plan.group_by

    # Keys common to from and to (same value in both) are BASE filters, not the period selector.
    # DeepSeek often puts product=HSD in both from and to; that is "diesel throughout", a base
    # filter — only the key whose value DIFFERS is the period axis.
    common = {k: v for k, v in plan.from_filter.items() if plan.to_filter.get(k) == v}
    base = {**plan.base_filters, **common}
    base.setdefault("row_kind", "entity")
    diff = [k for k in plan.to_filter if plan.to_filter.get(k) != plan.from_filter.get(k)]
    fkey = diff[0] if diff else "year"

    base_where = "WHERE " + " AND ".join(f'"{k}" = ?' for k in base)
    base_params = list(base.values())

    if plan.op == "level":
        expr = f'sum("{m}")'
        pergroup = f'SELECT "{g}" AS grp, {expr} AS v FROM {cat.table} {base_where} GROUP BY "{g}"'
        params = list(base_params)
        period_vals = []
    else:
        a = f'sum("{m}") FILTER (WHERE "{fkey}" = ?)'
        b = f'sum("{m}") FILTER (WHERE "{fkey}" = ?)'
        combine = {"percent_change": f"({a}-{b})/{b}*100", "difference": f"({a}-{b})",
                   "ratio": f"{a}/{b}"}[plan.op]
        seq = {"percent_change": ["to", "from", "from"], "difference": ["to", "from"],
               "ratio": ["to", "from"]}[plan.op]
        vals = {"to": plan.to_filter[fkey], "from": plan.from_filter[fkey]}
        filter_params = [vals[x] for x in seq]
        pergroup = f'SELECT "{g}" AS grp, {combine} AS v FROM {cat.table} {base_where} GROUP BY "{g}"'
        params = filter_params + base_params
        period_vals = [plan.from_filter[fkey], plan.to_filter[fkey]]

    outer = f"SELECT {plan.outer_agg}(v) FROM (\n  {pergroup}\n) WHERE v IS NOT NULL"
    t0 = time.perf_counter()
    answer = con.execute(outer, params).fetchone()[0]

    # PRECISE lineage: for each group, only the cells of the two periods used, for this measure.
    groups = con.execute(pergroup, params).fetchall()
    lineage = []
    for grp, v in groups:
        if v is None:
            continue
        wh = base_where + f' AND "{g}" = ?'
        if period_vals:
            wh += f' AND "{fkey}" IN (?,?)'
        rids = [r[0] for r in con.execute(
            f'SELECT __row_id FROM {cat.table} {wh}',
            base_params + [grp] + period_vals).fetchall()]
        cells = con.execute(
            "SELECT sheet,a1,raw_value FROM cell_map WHERE row_id IN (%s) AND column_name = ?"
            % ",".join("?" * len(rids)), rids + [m]).fetchall() if rids else []
        lineage.append((grp, round(v, 2), cells))
    con.close()
    ms = int((time.perf_counter() - t0) * 1000)
    return answer, lineage, outer, ms


if __name__ == "__main__":
    spec = Spec.load(root / "specs" / "ppac.yaml")
    db = root / "data" / "ppac_statewise_sales.duckdb"
    cat = build(db, spec.table, spec=spec)
    planner = make_planner("deepseek", None, None, False, allow_env=True)
    tier = next(t for t in planner.tiers if isinstance(t, DeepSeekPlanner))

    Q = "What is the average growth rate in diesel consumption across all states from 2019-20 to 2024-25?"
    raw = tier.client.chat.completions.create(
        model=tier.model, messages=[{"role": "user", "content": PROMPT % (cat.to_prompt(), Q)}],
        response_format={"type": "json_object"}, temperature=0, extra_body=tier.extra,
    ).choices[0].message.content
    plan = MultiStepPlan.model_validate_json(raw)
    print("PLAN:", plan.model_dump_json())
    ans, lineage, sql, ms = compile_and_run(plan, cat, db)
    print(f"\nANSWER: {plan.narration.format(v1=round(ans,2))}")
    print(f"raw = {ans}")
    print(f"\nLINEAGE TREE (outer {plan.outer_agg} over {len(lineage)} groups, each cited):")
    for grp, v, cells in lineage[:4]:
        print(f"  {grp}: {v}%  <- {[f'{c[0].split()[-1]}!{c[1]}' for c in cells]}")
    print(f"  … {len(lineage)-4} more groups" if len(lineage) > 4 else "")
    print(f"\ntotal cells cited: {sum(len(c) for _,_,c in lineage)}")
    print(f"exec {ms}ms | DeepSeek spend ${DeepSeekPlanner.spend_usd:.5f}")
    print(f"\nSQL:\n{sql}")
