"""Lineage as a shared-node DAG — the compact form that does NOT balloon.

The balloon (array_agg) copies a shared sub-result (a national total) into every row that uses it:
36 states x 36 total-cells = 1,332 references for ~36 real cells. This engine instead makes the
total ONE node that all 36 states point an edge to. Same information, no duplication — ProvSQL's
"circuit" idea, built with GProM's "read it from the query" idea, on DuckDB.

Two properties that kill the balloon:
  1. SHARING  — a sub-result is one node; users of it hold an edge, not a copy.
  2. LAZY     — a node's contributing cells are computed on demand (walk the edges), never
                materialised up front. You pay only for the branch you actually open.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import json, sys

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))
import duckdb


@dataclass
class Node:
    id: str
    kind: str                 # cell | sum | divide | avg | root
    label: str
    op: str = ""              # the computation on this node's inputs (÷, Σ, avg, …)
    value: float | None = None
    a1: str | None = None     # only for cell (leaf) nodes — its address in the sheet
    inputs: list[str] = field(default_factory=list)   # edges to child node ids


class DAG:
    def __init__(self):
        self.nodes: dict[str, Node] = {}
    def add(self, n: Node) -> str:
        self.nodes[n.id] = n; return n.id
    def cells(self, nid: str, _seen=None) -> set[str]:
        """Lazy, memo-free walk: a node's cells = union of its inputs' cells (leaves carry their own)."""
        n = self.nodes[nid]
        if n.kind == "cell":
            return {n.a1}
        out = set()
        for c in n.inputs:
            out |= self.cells(c)
        return out
    def edge_count(self):
        return sum(len(n.inputs) for n in self.nodes.values())
    def to_json(self):
        return {"nodes": [n.__dict__ for n in self.nodes.values()]}


def build_share_dag(con, base_where: str) -> DAG:
    """Each state's share = its cell ÷ (national total). The total is built from the 36 state cells
    and is added ONCE as a shared node; every state's divide points an edge to it."""
    g = DAG()
    rows = con.execute(
        f'SELECT state, value, __row_id FROM consumption WHERE {base_where} ORDER BY value DESC'
    ).fetchall()
    cells = con.execute(
        "SELECT row_id, sheet, a1, raw_value FROM cell_map WHERE row_id IN (%s) AND column_name='value'"
        % ",".join(str(r[2]) for r in rows)).fetchall()
    a1_by_rid = {rid: (sheet.split()[-1] + "!" + a1, float(v)) for rid, sheet, a1, v in cells}

    # leaves: one cell node per state
    leaf_ids = {}
    for state, val, rid in rows:
        a1, v = a1_by_rid[rid]
        nid = g.add(Node(id=f"cell:{state}", kind="cell", label=a1, a1=a1, value=v))
        leaf_ids[state] = nid

    total = sum(v for _, v, _ in rows)
    # THE SHARED NODE: the national total, built once from all 36 leaves
    tot = g.add(Node(id="sum:national", kind="sum", label=f"national total\n{total:,.0f}",
                     op="Σ", value=total, inputs=list(leaf_ids.values())))

    # per state: a divide node = its leaf ÷ the shared total
    share_ids = []
    for state, val, rid in rows:
        _, v = a1_by_rid[rid]
        nid = g.add(Node(id=f"share:{state}", kind="divide",
                         label=f"{state}\n{v/total*100:.2f}%", op="÷", value=v/total*100,
                         inputs=[leaf_ids[state], tot]))          # <-- edge to the SHARED total
        share_ids.append(nid)
    g.add(Node(id="root", kind="root", label="share of national\nper state", op="collect",
               inputs=share_ids))
    return g


if __name__ == "__main__":
    con = duckdb.connect(str(root / "data" / "ppac_statewise_sales.duckdb"), read_only=True)
    base = "product='HSD' AND row_kind='entity' AND year='2024-25'"
    g = build_share_dag(con, base)

    n_leaf = sum(n.kind == "cell" for n in g.nodes.values())
    print(f"DAG: {len(g.nodes)} nodes, {g.edge_count()} edges "
          f"({n_leaf} cells · 1 SHARED national total · {n_leaf} divides · 1 root)")
    # the anti-balloon proof: array_agg would copy the 36 total-cells into every state row
    balloon = n_leaf * (1 + n_leaf)
    print(f"array_agg would materialise {balloon} cell-references (36 x 37).")
    print(f"the DAG stores the national total ONCE — {g.nodes['sum:national'].__dict__['inputs'].__len__()} "
          f"edges into a single shared node, not {n_leaf} copies.")
    # lazy cells: open ONE state, resolve just its branch
    st = "GUJARAT"
    print(f"\nlazy: opening only {st} → cells {sorted(g.cells(f'share:{st}'))[:3]} … "
          f"({len(g.cells(f'share:{st}'))} cells, computed on demand, nothing else materialised)")
    out = root / "prototypes" / "share_dag.json"
    out.write_text(json.dumps(g.to_json()))
    print(f"\nwrote {out.name} for the visualisation")
