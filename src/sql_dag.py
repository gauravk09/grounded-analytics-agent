"""SQL -> operator DAG with lazy per-node provenance. The lineage engine for the SQL escape hatch.

Parameterised by workbook (table name + measure column), so it serves any spec. CTEs and scalar
subqueries become connected nodes; each node's contributing cells are computed on demand by re-running
its own base predicate. A window function marks a node opaque -> the caller degrades to flat lineage
or abstains rather than inventing a precise tree. See D83.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import sqlglot
from sqlglot import exp


@dataclass
class OpNode:
    id: str
    kind: str
    label: str
    op: str = ""
    inputs: list[str] = field(default_factory=list)
    predicate: str | None = None
    group_key: str | None = None
    opaque: bool = False


class OpDAG:
    def __init__(self, table: str, measure: str):
        self.nodes: dict[str, OpNode] = {}
        self.root: str | None = None
        self.table = table
        self.measure = measure

    def add(self, n): self.nodes[n.id] = n; return n.id
    def edges(self): return sum(len(n.inputs) for n in self.nodes.values())
    def has_opaque(self): return any(n.opaque for n in self.nodes.values())

    def rows(self, con, nid: str) -> set[int]:
        """Base __row_ids feeding this node — leaves run their own predicate, parents union."""
        n = self.nodes[nid]
        if n.opaque:
            return set()
        if n.kind in ("scan", "scalar") and n.predicate is not None:
            return {r[0] for r in con.execute(
                f'SELECT __row_id FROM {self.table} WHERE {n.predicate}').fetchall()}
        out = set()
        for c in n.inputs:
            out |= self.rows(con, c)
        return out

    def citations(self, con, nid: str):
        """(sheet, a1, raw_value, formula) for every measure cell feeding this node."""
        rids = self.rows(con, nid)
        if not rids:
            return []
        return con.execute(
            "SELECT sheet,a1,raw_value,formula FROM cell_map WHERE column_name = ? AND row_id IN (%s) "
            "ORDER BY sheet,a1" % ",".join(map(str, rids)), [self.measure]).fetchall()


def _base_where(sel: exp.Select, base_cols: set) -> str | None:
    w = sel.args.get("where")
    if not w:
        return None
    cols = {c.name for c in w.find_all(exp.Column)}
    return w.this.sql(dialect="duckdb") if cols and cols <= base_cols else None


def build(sql: str, base_cols: set, table: str, measure: str) -> OpDAG:
    g = OpDAG(table, measure)
    tree = sqlglot.parse_one(sql, read="duckdb")
    ctr = [0]
    def nid(p): ctr[0] += 1; return f"{p}{ctr[0]}"
    cte_map: dict[str, str] = {}

    def from_sources(sel):
        frm = sel.args.get("from_")
        exprs = []
        if frm:
            exprs += list(frm.find_all(exp.Table)) + [s.this for s in frm.find_all(exp.Subquery)]
        for j in sel.args.get("joins") or []:
            exprs += list(j.find_all(exp.Table)) + [s.this for s in j.find_all(exp.Subquery)]
        base = any(isinstance(e, exp.Table) and e.name == table for e in exprs)
        ctes = [e.name for e in exprs if isinstance(e, exp.Table) and e.name in cte_map]
        subs = [e for e in exprs if isinstance(e, exp.Select)]
        return base, ctes, subs

    def convert(sel: exp.Select) -> str:
        if list(sel.find_all(exp.Window)):
            return g.add(OpNode(nid("op"), "opaque", "window / running calc", opaque=True))
        base, ctes, subs = from_sources(sel)
        base_pred = _base_where(sel, base_cols) if base else None
        in_ids = [cte_map[c] for c in ctes] + [convert(s) for s in subs]
        for sub in sel.find_all(exp.Subquery):
            inner = sub.this
            if isinstance(inner, exp.Select) and inner not in subs:
                frm = inner.find(exp.From)
                pred = (_base_where(inner, base_cols)
                        if frm and any(t.name == table for t in frm.find_all(exp.Table)) else None)
                in_ids.append(g.add(OpNode(nid("scalar"), "scalar", "sub-total", op="Σ", predicate=pred)))
        gk = None
        grp = sel.args.get("group")
        if grp:
            for e in grp.expressions:
                if isinstance(e, exp.Column) and e.name in base_cols:
                    gk = e.name; break
        scan = g.add(OpNode(nid("scan"), "scan", ("group by " + gk) if gk else "scan",
                            op="filter", predicate=base_pred, group_key=gk, inputs=in_ids))
        if any(sel.find_all(exp.AggFunc)):
            return g.add(OpNode(nid("agg"), "aggregate", "aggregate", op="Σ/avg", inputs=[scan]))
        return scan

    for cte in tree.find_all(exp.CTE):
        if isinstance(cte.this, exp.Select):
            cte_map[cte.alias] = convert(cte.this)
    g.root = convert(tree if isinstance(tree, exp.Select) else tree.find(exp.Select))
    return g


def classify(g: OpDAG) -> str:
    if g.has_opaque():
        return "approximate"          # window/opaque -> flat fallback
    if any(n.group_key for n in g.nodes.values()):
        return "precise"              # per-group tree
    return "flat"                     # single scan / scalar
