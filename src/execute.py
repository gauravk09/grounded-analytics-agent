"""Run the two queries and assemble an Answer.

The value query gives the number. The provenance query — same WHERE, __row_id instead of the
aggregate — gives the rows behind it, which cell_map turns into cells. Nothing here computes
anything: DuckDB does the arithmetic, this module only wires results to their sources.
"""

from __future__ import annotations

from pathlib import Path

import re

import duckdb

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from answer import Answer, Citation, Value, compose_refusal   # noqa: E402
from catalog import Catalog                          # noqa: E402
from compile import Compiled, compile_measure, compile_plan   # noqa: E402
from plan import DerivedPlan, Plan, PlanFailure       # noqa: E402
from verify import (check_digits, check_lineage, find_absent_concept,   # noqa: E402
                    find_ambiguity, find_denominator_ambiguity, find_measure_gap,
                    find_scope_gap)



def _fmt(x) -> str:
    return f"{x:,.2f}" if isinstance(x, (int, float)) else str(x)


def _echo(c: Compiled, prov: dict | None = None) -> str:
    """The plan in plain English (D27), with each filter's source marked.

    An inherited filter is the one thing a follow-up adds that the user never typed, so it is
    exactly what has to be visible. Silent inheritance is the failure mode; this is the fix.
    """
    prov = prov or {}
    def mark(col: str) -> str:
        p = prov.get(col)
        return f" ({p})" if p in ("inherited", "default") else ""
    where = ", ".join(f"{f.column} {f.op} {f.value}{mark(f.column)}" for f in c.final_filters)
    s = f"{c.agg} of {c.metric_column} where {where}"
    if c.group_by:
        s += f", grouped by {', '.join(c.group_by)}"
    return s


def _provenance(question, plan, memory, catalog) -> dict:
    """Where each filter came from: stated / inherited / default / invented (D56)."""
    from memory import provenance
    from verify import named_in
    return provenance(question, plan, memory.previous if memory else None, catalog,
                      lambda term, sibs, al=None: named_in(question, term, sibs, al))


def _cells(con, row_ids: list[int], column: str) -> list[Citation]:
    if not row_ids:
        return []
    marks = ",".join("?" * len(row_ids))
    rows = con.execute(
        f"SELECT sheet, a1, raw_value, formula FROM cell_map "
        f"WHERE row_id IN ({marks}) AND column_name = ? ORDER BY sheet, a1",
        row_ids + [column],
    ).fetchall()
    return [Citation(*r) for r in rows]


OPS = {
    "divide":         lambda a, b: a / b,
    "subtract":       lambda a, b: a - b,
    "percent_change": lambda a, b: (a - b) / b,
}
OP_TEXT = {"divide": "{of} / {by}", "subtract": "{of} - {by}",
           "percent_change": "({of} - {by}) / {by}"}


def _run_measure(con, m, catalog: Catalog):
    """One aggregate plus the cells behind it. Same machinery as a whole plan (D29)."""
    c = compile_measure(m, catalog)
    row = con.execute(c.value_sql, c.params).fetchone()
    if row is None or row[0] is None:
        return None, None, c
    prov_sql, prov_params = c.provenance_sql()
    ids = [r[0] for r in con.execute(prov_sql, prov_params).fetchall()]
    return row[0], _cells(con, ids, c.metric_column), c


def _execute_derived(plan: DerivedPlan, catalog: Catalog, con, question: str,
                     prov: dict | None = None) -> Answer:
    by_name = {m.name: m for m in plan.measures}
    for ref in (plan.derive.of, plan.derive.by):
        if ref not in by_name:
            raise PlanFailure(f"derivation references unknown measure {ref!r}")

    parts, compiled = {}, {}
    for name, m in by_name.items():
        val, cits, c = _run_measure(con, m, catalog)
        if val is None:
            return Answer(question=question, status="abstained",
                          abstain_reason=compose_refusal("empty_result", "", catalog))
        unit = catalog.unit if c.metric_column == catalog.measure else None
        parts[name] = Value(val, _fmt(val), cits, unit,
                            sql=c.value_sql, params=c.params)
        compiled[name] = c

    a, b = parts[plan.derive.of].raw, parts[plan.derive.by].raw
    if b == 0:
        return Answer(question=question, status="abstained",
                      abstain_reason="the denominator is zero, so a ratio is undefined")

    # The arithmetic is ours, not the model's (D1). The model chose WHAT to divide; it never
    # sees either number.
    result = OPS[plan.derive.op](float(a), float(b)) * plan.derive.scale
    derived = Value(result, _fmt(result), citations=[], parts=parts,
                    derivation=OP_TEXT[plan.derive.op].format(of=plan.derive.of, by=plan.derive.by)
                    + (f" x {plan.derive.scale:g}" if plan.derive.scale != 1 else ""))

    echo = " ; ".join(f"{n} = {_echo(c, prov)}" for n, c in compiled.items())
    # No top-level sql: this answer took two statements, and showing only one of them would
    # misrepresent what ran. They live on the measures, next to the numbers they produced.
    return Answer(question=question, status="answered", narration=plan.narration,
                  slots={"v1": derived}, echo=f"{derived.derivation}, where {echo}")


def execute(plan: Plan, catalog: Catalog, db_path: Path, question: str = "",
            memory=None) -> Answer:
    if plan.kind == "abstain":
        return Answer(question=question, status="abstained",
                      abstain_reason=compose_refusal(plan.reason_code, plan.detail, catalog))

    scope = find_scope_gap(question, plan, catalog)
    if scope:
        col, labels = scope
        return Answer(question=question, status="clarify",
                      clarification=f"That would cover every {col} from {labels[0]} to "
                                    f"{labels[-1]} added together. Which {col} did you mean?",
                      scope_options=[f"all {col}s"] + labels[-3:][::-1])

    measures = find_measure_gap(question, plan, catalog)
    if measures:
        pretty = ", ".join(re.sub(r"\s+", " ", m).strip() for m in measures)
        return Answer(question=question, status="clarify",
                      clarification=f"This file has several measures. Which do you mean: {pretty}?",
                      scope_options=[re.sub(r"\s+", " ", m).strip() for m in measures])

    con = duckdb.connect(str(db_path), read_only=True)

    if plan.kind == "derived":
        amb = find_denominator_ambiguity(plan)
        if amb:
            con.close()
            return Answer(question=question, status="clarify",
                          clarification=f"That asks for {amb}")
        holes = set(re.findall(r"\{(v\d+)\}", plan.narration))
        if holes != {"v1"}:
            con.close()
            raise PlanFailure(f"a derived plan returns one value; narration uses {sorted(holes)}")
        a = _execute_derived(plan, catalog, con, question,
                             _provenance(question, plan, memory, catalog))
        con.close()
        if a.status == "answered":
            check_lineage(a, "sum")
            check_digits(a, question,
                         [f.value for m in plan.measures for f in m.filters])
        return a

    c = compile_plan(plan, catalog)
    rows = con.execute(c.value_sql, c.params).fetchall()
    if not rows or all(v is None for v in rows[0]):
        con.close()
        return Answer(question=question, status="abstained", sql=c.value_sql, params=c.params,
                      echo=_echo(c),
                      abstain_reason=compose_refusal("empty_result", "", catalog))

    # A breakdown ("by X") returns one row per group, but the narration carries a single
    # {v1} label + {v2} value slot and voices exactly one group. Presenting rows[0] would
    # silently drop the rest — the shape would lie (D9). Ask which group instead.
    if c.group_by and len(rows) > 1:
        col = c.group_by[0]
        labels = [str(r[0]) for r in rows]
        con.close()
        return Answer(question=question, status="clarify",
                      clarification=f"That breaks down into {len(rows)} groups by {col}. "
                                    f"I answer one value at a time — which {col}?",
                      scope_options=labels[:6])
    row = rows[0]

    # Narrow provenance to the group actually returned, else we would cite every group.
    group_values = {col: row[i] for i, col in enumerate(c.group_by)}
    prov_sql, prov_params = c.provenance_sql(group_values)
    row_ids = [r[0] for r in con.execute(prov_sql, prov_params).fetchall()]

    slots: dict[str, Value] = {}
    for name, source, val in zip(c.slots, c.slot_sources, row):
        if source == "__metric":
            # A computed number exists nowhere in the sheet — cite every cell that fed it.
            cits = _cells(con, row_ids, c.metric_column)
            unit = catalog.unit if c.metric_column == catalog.measure else None
        else:
            # A label came from one place; any contributing row carries the same cell.
            cits = _cells(con, row_ids[:1], source)
            unit = None
        slots[name] = Value(val, _fmt(val), cits, unit)

    con.close()
    prov = _provenance(question, plan, memory, catalog)
    a = Answer(question=question, status="answered", narration=plan.narration, slots=slots,
               sql=c.value_sql, params=c.params, echo=_echo(c, prov))
    # Both raise rather than abstain: a failure here is our bug, and saying "the data cannot
    # answer this" when the real problem is a broken citation would be a lie.
    check_lineage(a, c.agg)
    check_digits(a, question, [f.value for f in c.final_filters])
    return a


if __name__ == "__main__":
    from catalog import build
    from planner import get_planner

    root = Path(__file__).resolve().parent.parent
    from workbook import load
    _spec, db, _c = load()
    cat = build(db)
    planner = get_planner()

    for q in sys.argv[1:] or ["Which region used the most diesel in 2024-25?"]:
        a = execute(planner.plan(q, cat), cat, db, q)
        print(f"\nQ: {q}")
        print(f"   {a.text()}")
        if a.echo:
            print(f"   computed as: {a.echo}")
        for name, v in a.slots.items():
            print(f"   {name} = {v.formatted}  <- {len(v.citations)} cell(s)")
            for cit in v.citations[:12]:
                print(f"        {cit}")
