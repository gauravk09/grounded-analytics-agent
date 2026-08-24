"""Approach B — a frontier model writes SQL; my code VALIDATES it and tries to extract lineage.

Safety is the solvable half (research: the AST gate is the industry's real boundary): sqlglot
parses the SQL, a root-allowlist + full-tree denylist + table/column allowlist reject anything that
is not a read-only SELECT over the known schema, and the connection is opened READ_ONLY. Getting the
NUMBER is easy and a frontier model nails this class of query.

Lineage is the hard half. The blunt research verdict: per-CELL lineage from arbitrary LLM SQL is not
practical today (aggregation/DISTINCT/windows collapse the mapping, and DuckDB has no native
provenance). This prototype does the pragmatic thing — row-level why-provenance by pulling the base
rows the query's filters select — and shows exactly where it stops short of Approach A's per-group
tree.
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

BANNED = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter,
          exp.Command, exp.Set, exp.Pragma, exp.Copy)
BANNED_FUNCS = {"read_csv", "read_parquet", "read_json", "glob", "sniff_csv"}


def validate(sql: str, cat: Catalog) -> list[str]:
    """Return a list of reasons the SQL is unsafe/ungrounded; empty = safe. (sqlglot AST gate.)"""
    reasons = []
    try:
        trees = sqlglot.parse(sql, read="duckdb")
    except Exception as e:
        return [f"unparseable: {e}"]
    if len(trees) != 1:
        reasons.append("must be exactly one statement")
    tree = trees[0]
    if not isinstance(tree, (exp.Select, exp.With, exp.Subquery)):
        reasons.append(f"root is {type(tree).__name__}, only SELECT/CTE allowed")
    for node in tree.walk():
        if isinstance(node, BANNED):
            reasons.append(f"banned node {type(node).__name__}")
        if isinstance(node, exp.Anonymous) and node.name.lower() in BANNED_FUNCS:
            reasons.append(f"banned function {node.name}")
    # table + column allowlist (the grounding check == the abstention hook)
    tables = {t.name for t in tree.find_all(exp.Table)}
    for t in tables:
        if t not in {cat.table} and t not in {n.alias_or_name for n in tree.find_all(exp.CTE)}:
            reasons.append(f"unknown table {t!r}")
    # Only BASE-table columns must be in the schema; aliases the query itself defines are fine.
    aliases = {a.alias for a in tree.find_all(exp.Alias) if a.alias}
    aliases |= {t.alias for t in tree.find_all(exp.TableAlias) if t.alias}
    cols = {c.name for c in tree.find_all(exp.Column)}
    known = cat.column_names | {"__row_id"} | aliases
    unknown = [c for c in cols if c not in known and not c.isdigit()]
    if unknown:
        reasons.append(f"unknown columns {unknown}")
    return reasons


def base_filters(sql: str) -> list[tuple[str, str]]:
    """Best-effort: equality filters on the base table, for row-level why-provenance."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    out = []
    for eq in tree.find_all(exp.EQ):
        if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Literal):
            out.append((eq.this.name, eq.expression.this))
    return out


def run(cat: Catalog, db: Path, question: str):
    planner = make_planner("deepseek", None, None, False, allow_env=True)
    tier = next(t for t in planner.tiers if isinstance(t, DeepSeekPlanner))
    prompt = (f"Write ONE read-only DuckDB SQL query to answer the question. Use only this schema.\n"
              f"Return SQL only, no prose, no markdown.\n\nSCHEMA:\n{cat.to_prompt()}\n\n"
              f"Rows where row_kind='entity' are real entities (exclude totals). "
              f"QUESTION: {question}")
    t0 = time.perf_counter()
    sql = tier.client.chat.completions.create(
        model=tier.model, messages=[{"role": "user", "content": prompt}],
        temperature=0, extra_body=tier.extra).choices[0].message.content.strip()
    if sql.startswith("```"):
        sql = sql.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
        if sql.lstrip().lower().startswith("sql"):
            sql = sql.split("\n", 1)[1]
    gen_ms = int((time.perf_counter() - t0) * 1000)
    print("GENERATED SQL:\n", sql.strip(), "\n")

    reasons = validate(sql, cat)
    print("VALIDATION:", "SAFE ✓" if not reasons else f"REJECTED — {reasons}")
    if reasons:
        return

    con = duckdb.connect(str(db), read_only=True)
    con.execute("SET enable_external_access=false")
    t1 = time.perf_counter()
    result = con.execute(sql).fetchall()
    exec_ms = int((time.perf_counter() - t1) * 1000)
    print("ANSWER:", result[0] if len(result) == 1 else f"{len(result)} rows: {result[:3]}")

    # lineage attempt: row-level why-provenance from the base filters
    filt = base_filters(sql)
    where = " AND ".join(f'"{k}" = ?' for k, _ in filt) or "TRUE"
    rids = [r[0] for r in con.execute(
        f'SELECT __row_id FROM {cat.table} WHERE {where}', [v for _, v in filt]).fetchall()]
    ncells = con.execute(
        f"SELECT count(*) FROM cell_map WHERE row_id IN (SELECT __row_id FROM {cat.table} WHERE {where})",
        [v for _, v in filt]).fetchone()[0] if rids else 0
    con.close()
    print(f"\nLINEAGE (row-level why-provenance): {len(rids)} base rows, ~{ncells} cells contributed")
    print(f"  extracted from filters {filt}")
    print("  NOTE: this is a FLAT set for the whole answer — NOT the per-state tree Approach A gives.")
    print("  Per-group / per-cell attribution would need provenance rewriting (ProvSQL/GProM-style).")
    print(f"\ngen {gen_ms}ms + exec {exec_ms}ms | DeepSeek spend ${DeepSeekPlanner.spend_usd:.5f}")


if __name__ == "__main__":
    spec = Spec.load(root / "specs" / "ppac.yaml")
    db = root / "data" / "ppac_statewise_sales.duckdb"
    cat = build(db, spec.table, spec=spec)
    run(cat, db, "What is the average growth rate in diesel consumption across all states from 2019-20 to 2024-25?")
