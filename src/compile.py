"""Turn a validated Plan into SQL.

Three jobs, in order:
  1. Reject any column name not in the catalog whitelist (PlanFailure, not Abstain — an
     invented column is a model malfunction, so the cascade should escalate).
  2. Inject the compiler defaults (D9 row_kind='state', D24 product='ALL'), so the two
     aggregate traps in this workbook cannot be hit by forgetting a filter.
  3. Emit two statements from one plan — the value query, and the provenance query that is
     the same query with the aggregate swapped for __row_id. They cannot drift apart.

Values are always bound as parameters, never interpolated. Column names are interpolated,
but only after passing the whitelist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog import Catalog                                  # noqa: E402
from plan import DerivedPlan, Filter, Measure, PlanFailure, QueryPlan   # noqa: E402


@dataclass
class Compiled:
    value_sql: str
    params: list
    slots: list[str]            # ["v1", "v2", ...] in SELECT order
    slot_sources: list[str]     # what each slot is: a column name, or "__metric"
    group_by: list[str]
    final_filters: list          # filters AFTER defaults were injected — used for plan-echo
    agg: str
    metric_column: str
    _where: str                 # shared by both queries — the reason lineage holds
    _table: str

    def provenance_sql(self, group_values: dict | None = None) -> tuple[str, list]:
        """Rows that produced the answer. Same WHERE, __row_id instead of the aggregate.

        When the plan grouped, `group_values` narrows provenance to the group actually
        returned — otherwise we would cite every row in the table, not the ones behind
        the number on screen.
        """
        # A grouped query's WHERE matches every group, not the one on screen. Handing back
        # those rows would be citations that are technically "what the query scanned" and
        # completely wrong as an answer to "where did this number come from?". The object
        # knows it was grouped, so it refuses rather than allowing a quiet mistake.
        missing = set(self.group_by) - set(group_values or {})
        if missing:
            raise ValueError(
                f"provenance for a grouped query needs the group actually returned; "
                f"missing {sorted(missing)}"
            )
        where, params = self._where, list(self.params)
        for col, val in (group_values or {}).items():
            where += f' AND "{col}" = ?'
            params.append(val)
        return f'SELECT __row_id FROM {self._table} {where}', params


def check_derived(plan: DerivedPlan, catalog: Catalog) -> None:
    """Shape-check a derived plan before anything runs (D50)."""
    from verify import check_derivation_shape
    problem = check_derivation_shape(plan, catalog)
    if problem:
        raise PlanFailure(problem)


def compile_measure(m: Measure, catalog: Catalog) -> Compiled:
    """One aggregate, no grouping. A measure is just a plan with fewer parts, so it goes
    through exactly the same whitelist, defaults and contradiction checks."""
    return _compile(m.agg, m.column, list(m.filters), [], "none", 0, catalog)


def compile_plan(plan: QueryPlan, catalog: Catalog) -> Compiled:
    c = _compile(plan.metric.agg, plan.metric.column, list(plan.filters),
                 list(plan.group_by), plan.sort, plan.limit, catalog)

    # The narration's holes must line up exactly with the values this query returns.
    # Nothing else enforces the prompt/compiler slot contract (D31), so it is enforced here:
    # a mismatch is a model malfunction, so PlanFailure escalates rather than abstaining.
    holes = set(re.findall(r"\{(v\d+)\}", plan.narration))
    if holes != set(c.slots):
        raise PlanFailure(
            f"narration uses {sorted(holes)} but this query returns {c.slots}; "
            f"holes are only for values the query returns, not for filter values"
        )
    return c


def _compile(agg: str, column: str, filters: list, group_by: list, sort: str,
             limit: int, catalog: Catalog) -> Compiled:
    allowed = catalog.column_names

    def check(name: str, where: str) -> str:
        if name not in allowed:
            raise PlanFailure(f"{where} references unknown column {name!r}; "
                              f"allowed: {sorted(allowed)}")
        return name

    check(column, "metric")
    for g in group_by:
        check(g, "group_by")

    # --- filters, with the defaults injected ------------------------------------
    stated = {f.column for f in filters}
    for col, default in catalog.defaults.items():
        if col not in stated and col in allowed:
            filters.append(Filter(column=col, op="=", value=default))
    for f in filters:
        check(f.column, "filter")

    where = "WHERE " + " AND ".join(f'"{f.column}" {f.op} ?' for f in filters) if filters else ""
    params = [f.value for f in filters]

    # --- select list: group columns first, then the metric ----------------------
    # Slot order follows SELECT order, which is what the narration's {v1}/{v2} assume.
    select, slots, sources = [], [], []
    for g in group_by:
        slots.append(f"v{len(slots) + 1}")
        select.append(f'"{g}" AS {slots[-1]}')
        sources.append(g)
    slots.append(f"v{len(slots) + 1}")
    select.append(f'{agg}("{column}") AS {slots[-1]}')
    sources.append("__metric")

    sql = f'SELECT {", ".join(select)} FROM {catalog.table} {where}'
    if group_by:
        sql += " GROUP BY " + ", ".join(f'"{g}"' for g in group_by)
    if sort != "none":
        sql += f" ORDER BY {len(select)} {sort.upper()}"   # by the metric column
    if limit:
        sql += f" LIMIT {int(limit)}"

    # Naming an entity while asking for a non-entity row can never match: aggregate rows carry no
    # entity name (D19). The model reaches for a total row-kind whenever the question says "total",
    # so this fires in practice. Statically impossible, so catch it rather than returning zero rows
    # and blaming the data. Written in terms of `catalog.entity`, so it holds for a workbook whose
    # rows are budget lines just as it did for one whose rows are states.
    by_col = {f.column: f.value for f in filters}
    entity_kind = catalog.defaults.get("row_kind", "entity")
    if by_col.get(catalog.entity) and by_col.get("row_kind", entity_kind) != entity_kind:
        raise PlanFailure(
            f"{catalog.entity}={by_col[catalog.entity]!r} with "
            f"row_kind={by_col['row_kind']!r} can never match: aggregate rows carry no "
            f"{catalog.entity}. Filter on one or the other, not both"
        )

    # "Which region used the least diesel" means the smallest regional TOTAL, not the smallest
    # single row within a region. min/max as the metric while grouping and ranking produces the
    # group containing the extreme row — a wrong answer that passes every other gate. Caught
    # here because coverage cannot see a wrong aggregation (D27).
    if agg in ("min", "max") and group_by and limit:
        raise PlanFailure(
            f"agg={agg} with group_by={group_by} and limit={limit} ranks "
            f"groups by their extreme row, not their total; use sum or avg as the metric and "
            f"let sort+limit pick the extreme group"
        )

    return Compiled(sql, params, slots, sources, list(group_by),
                    filters, agg, column,
                    where, catalog.table)


if __name__ == "__main__":
    import json
    from catalog import build
    from planner import get_planner

    root = Path(__file__).resolve().parent.parent
    from workbook import load
    _spec, _db, cat = load()
    q = sys.argv[1] if len(sys.argv) > 1 else "What was Gujarat's diesel consumption in 2019-20?"
    p = get_planner().plan(q, cat)
    if p.kind != "query":
        print(f"ABSTAIN: {p.reason}")
        raise SystemExit
    c = compile_plan(p, cat)
    print("Q:", q)
    print("\nvalue      :", c.value_sql)
    print("params     :", c.params)
    print("slots      :", list(zip(c.slots, c.slot_sources)))
    ps, pp = c.provenance_sql()
    print("provenance :", ps)
    print("params     :", pp)
