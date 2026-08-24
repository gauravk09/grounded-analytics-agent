"""SQL -> operator DAG, the keystone. Parse an LLM's SQL into a graph of relational operators where
CTEs and scalar subqueries are SHARED nodes (the AST already writes them once), attach lazy per-node
provenance (which base cells feed each node), and DEGRADE gracefully — mark a node opaque and fall
back to flat why-provenance for shapes we don't model (window, distinct-heavy, deep nesting).

This is what turns the precise-lineage tree from 3/10 into general: we follow the query's real
operator structure instead of pattern-matching one shape.

Single base table (`consumption`) with `__row_id`, so a node's provenance is the set of base rows its
subtree scans — computed lazily by re-running that node's predicate (GProM's read-from-the-query
idea), and shared where the AST shares (ProvSQL's circuit idea).
"""
from __future__ import annotations
from dataclasses import dataclass, field
import sys
from pathlib import Path
import sqlglot
from sqlglot import exp

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src")); sys.path.insert(0, str(root / "prototypes"))
from dotenv import load_dotenv; load_dotenv()
import duckdb
BASE = "consumption"
OPAQUE_NODES = (exp.Window,)                       # shapes our simple provenance can't trace exactly


@dataclass
class OpNode:
    id: str
    kind: str                    # scan | aggregate | derive | scalar | cte | project | opaque
    label: str
    op: str = ""
    inputs: list[str] = field(default_factory=list)
    predicate: str | None = None   # base-table WHERE for scan/scalar nodes
    group_key: str | None = None   # base column this node groups on, if any
    opaque: bool = False


class OpDAG:
    def __init__(self): self.nodes: dict[str, OpNode] = {}; self.root: str | None = None
    def add(self, n): self.nodes[n.id] = n; return n.id
    def edges(self): return sum(len(n.inputs) for n in self.nodes.values())
    def shared(self):  # nodes with >1 parent = genuinely shared
        indeg = {k: 0 for k in self.nodes}
        for n in self.nodes.values():
            for c in n.inputs: indeg[c] += 1
        return [k for k, d in indeg.items() if d > 1]
    def has_opaque(self): return any(n.opaque for n in self.nodes.values())

    def cells(self, con, nid: str) -> set[str]:
        """Lazy provenance for one node: base rows it scans -> their value cells."""
        n = self.nodes[nid]
        if n.opaque:
            return set()  # can't trace precisely
        if n.kind in ("scan", "scalar") and n.predicate is not None:
            rids = [r[0] for r in con.execute(
                f"SELECT __row_id FROM {BASE} WHERE {n.predicate}").fetchall()]
            if not rids: return set()
            return {f"{s.split()[-1]}!{a}" for s, a in con.execute(
                "SELECT sheet,a1 FROM cell_map WHERE column_name='value' AND row_id IN (%s)"
                % ",".join(map(str, rids))).fetchall()}
        out = set()
        for c in n.inputs: out |= self.cells(con, c)
        return out


def _base_where(sel: exp.Select, base_cols: set) -> str | None:
    """The WHERE of a select, kept only if it filters base columns (so it re-runs on the base table)."""
    w = sel.args.get("where")
    if not w: return None
    cols = {c.name for c in w.find_all(exp.Column)}
    return w.this.sql(dialect="duckdb") if cols and cols <= base_cols else None


def build(sql: str, base_cols: set) -> OpDAG:
    g = OpDAG()
    tree = sqlglot.parse_one(sql, read="duckdb")
    ctr = [0]
    def nid(p): ctr[0] += 1; return f"{p}{ctr[0]}"
    cte_map: dict[str, str] = {}

    def from_sources(sel):
        """(base?, [cte names], [subquery Selects]) directly in this select's FROM + JOINs."""
        frm = sel.args.get("from_")
        exprs = []
        if frm:
            exprs += list(frm.find_all(exp.Table))
            exprs += [s.this for s in frm.find_all(exp.Subquery)]
        for j in sel.args.get("joins") or []:
            exprs += list(j.find_all(exp.Table)) + [s.this for s in j.find_all(exp.Subquery)]
        base = any(isinstance(e, exp.Table) and e.name == BASE for e in exprs)
        ctes = [e.name for e in exprs if isinstance(e, exp.Table) and e.name in cte_map]
        subs = [e for e in exprs if isinstance(e, exp.Select)]
        return base, ctes, subs

    def convert(sel: exp.Select) -> str:
        if list(sel.find_all(exp.Window)):
            return g.add(OpNode(nid("op"), "opaque", "window / running calc", opaque=True))
        base, ctes, subs = from_sources(sel)
        base_pred = _base_where(sel, base_cols) if base else None
        # inputs: CTE references (edges to shared CTE nodes) + nested FROM-subqueries
        in_ids = [cte_map[c] for c in ctes] + [convert(s) for s in subs]
        # scalar subqueries used as VALUES (shared sub-totals), excluding FROM-subqueries
        for sub in sel.find_all(exp.Subquery):
            inner = sub.this
            if isinstance(inner, exp.Select) and inner not in subs:
                pred = _base_where(inner, base_cols) if inner.args.get("from_") and \
                    any(t.name == BASE for t in inner.find(exp.From).find_all(exp.Table)) else None
                in_ids.append(g.add(OpNode(nid("scalar"), "scalar", "sub-total", op="Σ", predicate=pred)))
        gk = None
        grp = sel.args.get("group")
        if grp:
            for e in grp.expressions:
                if isinstance(e, exp.Column) and e.name in base_cols: gk = e.name; break
        scan = g.add(OpNode(nid("scan"), "scan", ("group by " + gk) if gk else "scan",
                            op="filter", predicate=base_pred, group_key=gk, inputs=in_ids))
        aggs = any(sel.find_all(exp.AggFunc))
        if aggs:
            return g.add(OpNode(nid("agg"), "aggregate", "aggregate", op="Σ/avg", inputs=[scan]))
        return scan

    for cte in tree.find_all(exp.CTE):
        if isinstance(cte.this, exp.Select):
            cte_map[cte.alias] = convert(cte.this)
    main = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    g.root = convert(main)
    return g


def classify(g: OpDAG) -> str:
    if g.has_opaque(): return "degraded (opaque node → flat fallback)"
    if any(n.group_key for n in g.nodes.values()): return "precise tree (grouped)"
    return "flat (single scan / scalar)"


if __name__ == "__main__":
    from spec import Spec; from catalog import build as build_cat
    from planner import make_planner, DeepSeekPlanner
    from lineage_stress import gen_sql, CASES
    from approach_b import validate
    spec = Spec.load(root/"specs"/"ppac.yaml"); db = root/"data"/"ppac_statewise_sales.duckdb"
    cat = build_cat(db, spec.table, spec=spec)
    tier = next(t for t in make_planner("deepseek",None,None,False,allow_env=True).tiers if isinstance(t,DeepSeekPlanner))
    con = duckdb.connect(str(db), read_only=True)

    prec=flat=deg=0
    for i,q in enumerate(CASES,1):
        sql = gen_sql(cat, tier, q)
        if validate(sql, cat): print(f"{i:>2}. REJECTED by gate"); continue
        try:
            g = build(sql, cat.column_names); cls = classify(g)
        except Exception as e:
            print(f"{i:>2}. {q[:44]:44} | parse error: {type(e).__name__}"); continue
        prec += cls.startswith("precise"); flat += cls.startswith("flat"); deg += cls.startswith("degraded")
        print(f"{i:>2}. {q[:44]:44} | {len(g.nodes):>2} nodes {g.edges():>2} edges "
              f"{len(g.shared())} shared | {cls}")
    print(f"\nprecise-tree {prec} · flat {flat} · degraded {deg}  (of {len(CASES)}) · "
          f"spend ${DeepSeekPlanner.spend_usd:.4f}")
