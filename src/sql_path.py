"""The SQL escape hatch, behind the validated-plan seam (D22, D79-D83).

When the typed planner cannot express a question (deep aggregates: average growth across all states,
period-over-period, share-as-a-number), a frontier model writes SQL, we VALIDATE it (sql_gate), run it
read-only, and recover lineage from the operator DAG (sql_dag). The seam is unchanged in spirit: what
runs is always a *validated* artifact, and the model still never emits a digit — DuckDB computes the
number, and the answer ships only if its cells are traceable. If any of that fails, we return None and
the honest abstention stands. Scalar answers only for now; multi-row (lists) defer.
"""
from __future__ import annotations
import json
from pathlib import Path

import duckdb

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from answer import Answer, Citation, Value                     # noqa: E402
from catalog import Catalog                                    # noqa: E402
from sql_gate import validate                                  # noqa: E402
from sql_dag import build, classify                            # noqa: E402

PROMPT = """You are given the schema of ONE table. Write a single read-only DuckDB SQL query that
answers the question, and a one-line narration with exactly one {v1} placeholder and NO digits.
Rows where row_kind='entity' are real entities (exclude totals). Return JSON only:
{"sql": "...", "narration": "... {v1} ..."}

SCHEMA:
%s

QUESTION: %s"""


def _tier(planner):
    try:
        from planner import DeepSeekPlanner
    except Exception:
        return None
    return next((t for t in getattr(planner, "tiers", [planner])
                 if isinstance(t, DeepSeekPlanner)), None)


def _fmt(x):
    return f"{x:,.2f}" if isinstance(x, (int, float)) else str(x)


def answer_via_sql(question: str, catalog: Catalog, db_path: Path, planner) -> Answer | None:
    tier = _tier(planner)
    if tier is None:
        return None                       # the escape hatch needs a capable model; else keep abstain
    try:
        raw = tier.client.chat.completions.create(
            model=tier.model, temperature=0, extra_body=tier.extra,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": PROMPT % (catalog.to_prompt(), question)}],
        ).choices[0].message.content
        obj = json.loads(raw)
        sql, narration = obj.get("sql", "").strip(), obj.get("narration", "").strip()
    except Exception:
        return None
    if not sql:
        return None

    # 1. validate (safety + grounding) — a bad or ungrounded query never runs
    if validate(sql, catalog.table, catalog.column_names):
        return None

    # 2. execute read-only; accept a single scalar row only (defer lists)
    try:
        con = duckdb.connect(str(db_path), read_only=True)
        con.execute("SET enable_external_access=false")
        rows = con.execute(sql).fetchall()
    except Exception:
        return None
    if len(rows) != 1:
        con.close(); return None
    nums = [v for v in rows[0] if isinstance(v, (int, float))]
    if not nums:
        con.close(); return None
    value = nums[-1]

    # 3. recover lineage from the operator DAG — no cells, no answer (the product's promise)
    try:
        g = build(sql, catalog.column_names, catalog.table, catalog.measure)
        cites = g.citations(con, g.root)
        cls = classify(g)
    except Exception:
        con.close(); return None
    con.close()
    if not cites:
        return None                       # untraceable -> abstain rather than answer

    citations = [Citation(sheet=s, a1=a, raw_value=rv, formula=fx) for s, a, rv, fx in cites]
    if "{v1}" not in narration:
        narration = "The result is {v1}."
    v = Value(raw=value, formatted=_fmt(value), citations=citations, unit=catalog.unit)
    note = {"precise": "traced to each source cell", "flat": "traced to the rows it scanned",
            "approximate": "approximate lineage (contains a windowed step)"}[cls]
    return Answer(question=question, status="answered", narration=narration,
                  slots={"v1": v}, sql=sql,
                  echo=f"answered by writing SQL (beyond the typed planner) · {note}")
