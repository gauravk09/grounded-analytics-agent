"""The safety gate for LLM-written SQL: a sqlglot AST allowlist. The industry's real boundary
(prompt wording is not) — root must be SELECT/CTE, no DML/DDL/PRAGMA/COPY/ATTACH or file-reading
functions, single statement, and every referenced base table/column must be in the catalog
(unknown identifier == ungrounded == refuse). Returns reasons; empty means safe. See D79/B research.
"""
from __future__ import annotations
import sqlglot
from sqlglot import exp

BANNED = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter,
          exp.Command, exp.Set, exp.Pragma, exp.Copy)
BANNED_FUNCS = {"read_csv", "read_parquet", "read_json", "glob", "sniff_csv"}


def validate(sql: str, table: str, columns: set) -> list[str]:
    reasons = []
    try:
        trees = sqlglot.parse(sql, read="duckdb")
    except Exception as e:
        return [f"unparseable: {e}"]
    if len(trees) != 1 or trees[0] is None:
        return ["must be exactly one statement"]
    tree = trees[0]
    if not isinstance(tree, (exp.Select, exp.With, exp.Subquery)):
        reasons.append(f"root is {type(tree).__name__}; only SELECT/CTE allowed")
    for node in tree.walk():
        if isinstance(node, BANNED):
            reasons.append(f"banned: {type(node).__name__}")
        if isinstance(node, exp.Anonymous) and node.name.lower() in BANNED_FUNCS:
            reasons.append(f"banned function {node.name}")
    cte_names = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    for t in {t.name for t in tree.find_all(exp.Table)}:
        if t != table and t not in cte_names:
            reasons.append(f"unknown table {t!r}")
    aliases = {a.alias for a in tree.find_all(exp.Alias) if a.alias}
    aliases |= {t.alias for t in tree.find_all(exp.TableAlias) if t.alias}
    known = columns | {"__row_id"} | aliases
    unknown = [c.name for c in tree.find_all(exp.Column) if c.name not in known and not c.name.isdigit()]
    if unknown:
        reasons.append(f"unknown columns {sorted(set(unknown))}")
    return reasons
