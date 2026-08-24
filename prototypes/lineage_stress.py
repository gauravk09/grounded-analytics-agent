"""Stress-test lineage recovery from LLM SQL (Approach B), the research way: reuse the query's OWN
base-table predicate (so IN/OR/ranges work), then find which base rows fed the answer.

Method (why-provenance for a single base table): find every scan of the base table in the AST, take
the WHERE that filters it, and re-run it as `SELECT __row_id FROM base WHERE <that predicate>`. Those
rows' cells are the provenance. Reusing the real predicate text is what fixes the naive `year IN(...)`
break. We also try the STRUCTURED tree (per group-by key) when the query groups on a base column.

We run ~10 hard shapes and report: number ok? lineage recovered? and where it degrades.
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import sqlglot
from sqlglot import exp

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv()
import duckdb
from spec import Spec
from catalog import build, Catalog
from planner import make_planner, DeepSeekPlanner

sys.path.insert(0, str(root / "prototypes"))
from approach_b import validate, BANNED, BANNED_FUNCS   # reuse the safety gate

BASE = "consumption"


def gen_sql(cat, tier, q):
    prompt = (f"Write ONE read-only DuckDB SQL query. Use only this schema. SQL only, no markdown.\n"
              f"Rows with row_kind='entity' are real entities (exclude totals).\n\n{cat.to_prompt()}\n\nQ: {q}")
    sql = tier.client.chat.completions.create(model=tier.model, temperature=0, extra_body=tier.extra,
          messages=[{"role":"user","content":prompt}]).choices[0].message.content.strip()
    if sql.startswith("```"):
        sql = sql.strip("`"); sql = sql.split("\n",1)[1] if sql.lower().startswith("sql") else sql
        sql = sql.rsplit("```",1)[0]
    return sql.strip().rstrip(";")


def base_predicates(sql: str, base_cols: set):
    """Every WHERE that filters the base table. Single base table => a WHERE made only of base-column
    predicates narrows the base rows. We keep WHOLE where-clauses whose columns are all base columns
    (so IN/OR/ranges are preserved verbatim), and skip where-clauses over derived aliases (HAVING-like)."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    conds = []
    for w in tree.find_all(exp.Where):
        cols = {c.name for c in w.find_all(exp.Column)}
        if cols and cols <= base_cols:                 # only base columns -> it's a base filter
            conds.append(w.this.sql(dialect="duckdb"))
    return conds


def provenance(con, sql: str, base_cols: set):
    """Base rows feeding the answer = rows matching the query's own base predicate (OR of scans)."""
    conds = base_predicates(sql, base_cols)
    if not conds:
        return None, "no base-table WHERE found (whole-table or non-base scan)"
    pred = " OR ".join(f"({c})" for c in conds)
    try:
        rids = [r[0] for r in con.execute(f"SELECT DISTINCT __row_id FROM {BASE} WHERE {pred}").fetchall()]
    except Exception as e:
        return None, f"predicate re-run failed: {e}"
    return rids, f"{len(rids)} rows via reused predicate"


def structured(con, sql: str, base_cols: set):
    """Per-group tree: base predicate + the base column the query groups on -> array_agg(__row_id)
    per group. Precise (each group cites only its rows), vs the flat scan-wide set."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    conds = base_predicates(sql, base_cols)
    if not conds:
        return None
    pred = " OR ".join(f"({c})" for c in conds)
    # a GROUP BY expression that is a bare base column
    key = None
    for gb in tree.find_all(exp.Group):
        for e in gb.expressions:
            if isinstance(e, exp.Column) and e.name in base_cols:
                key = e.name; break
        if key: break
    if not key:
        return None
    try:
        return [(r[0], r[1]) for r in con.execute(
            f'SELECT "{key}" AS grp, array_agg(__row_id) AS rids FROM {BASE} '
            f'WHERE {pred} GROUP BY "{key}"').fetchall()]
    except Exception:
        return None


CASES = [
    "How much diesel did Gujarat consume in 2024-25?",                       # simple
    "Which state consumed the most petrol in 2024-25?",                      # group+rank
    "What is the average growth in diesel across all states, 2019-20 to 2024-25?",  # per-group+agg
    "What share of national diesel did Maharashtra hold in 2024-25?",        # ratio
    "How many states consumed more than 10000 units of diesel in 2024-25?",  # HAVING/count>threshold
    "List the top 3 states by petrol consumption in 2024-25 and their totals.",  # top-N
    "For each region, what is the total diesel in 2024-25?",                 # group by region
    "Which states had diesel consumption above the national average in 2024-25?",  # subquery/window-ish
    "Rank all states by their diesel growth from 2019-20 to 2024-25.",       # window/rank
    "What is the year-over-year change in total diesel between 2023-24 and 2024-25?",  # two-period
]


if __name__ == "__main__":
    spec = Spec.load(root/"specs"/"ppac.yaml"); db = root/"data"/"ppac_statewise_sales.duckdb"
    cat = build(db, spec.table, spec=spec)
    tier = next(t for t in make_planner("deepseek",None,None,False,allow_env=True).tiers
                if isinstance(t, DeepSeekPlanner))
    con = duckdb.connect(str(db), read_only=True); con.execute("SET enable_external_access=false")

    ok_num=ok_lin=ok_tree=0
    for i,q in enumerate(CASES,1):
        sql = gen_sql(cat, tier, q)
        reasons = validate(sql, cat)
        safe = not reasons
        try:
            res = con.execute(sql).fetchall() if safe else None
            num_ok = safe and res is not None
        except Exception as e:
            res, num_ok = f"ERR {e}", False
        rids, note = provenance(con, sql, cat.column_names) if num_ok else (None, "skipped")
        tree = structured(con, sql, cat.column_names) if num_ok else None
        ok_num+=bool(num_ok); ok_lin+=bool(rids); ok_tree+=bool(tree)
        shape = "tree" if tree else ("flat" if rids else "—")
        print(f"{i:>2}. {q[:52]:52} | {'SAFE' if safe else 'REJECT':6} | "
              f"num={'ok' if num_ok else 'x':2} | lineage={shape:4} | {note}")
    print(f"\nnumbers {ok_num}/{len(CASES)} · flat-lineage {ok_lin}/{len(CASES)} · "
          f"structured-tree {ok_tree}/{len(CASES)} · spend ${DeepSeekPlanner.spend_usd:.4f}")
