"""Display-only formatting, shared by every renderer.

One definition, because two drifted once already (D57): `named_in` and `overfilter_gaps` each
grew their own idea of what "stated" meant, and the bug lived in the gap. A Streamlit renderer and
a React API returning subtly different SQL text is the same failure with a longer feedback loop.

Nothing here produces a NUMBER. Values are formatted by `execute()` and travel on `Value.formatted`
— if a renderer ever formatted a figure itself, it would become a second place numbers are made,
and `check_digits` would correctly fire.
"""

from __future__ import annotations

import re

CLAUSES = ("FROM", "WHERE", "GROUP BY", "ORDER BY", "LIMIT")


def cell_ranges(citations) -> list[dict]:
    """Collapse contiguous same-column cells into ranges (S10:S19) so a reference points at a block,
    not 36 separate cells — and a sheet view can highlight the whole run at once. Runs break where the
    rows are non-consecutive (a section header or total sits between entity rows), which is correct:
    each range is a real contiguous block in the file."""
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for c in citations:
        m = re.match(r"([A-Z]+)(\d+)$", c.a1)
        if not m:
            groups[(c.sheet, c.a1)].append((0, c.a1))
            continue
        groups[(c.sheet, m.group(1))].append((int(m.group(2)), c.a1))
    out = []
    for (sheet, _col), items in groups.items():
        items.sort()
        run = [items[0]]
        for it in items[1:]:
            if it[0] == run[-1][0] + 1:
                run.append(it)
            else:
                out.append(_mk(sheet, run)); run = [it]
        out.append(_mk(sheet, run))
    return sorted(out, key=lambda r: (r["sheet"], r["cells"][0]))


def _mk(sheet, run) -> dict:
    a1s = [a for _, a in run]
    ref = a1s[0] if len(a1s) == 1 else f"{a1s[0]}:{a1s[-1]}"
    return {"sheet": sheet, "ref": ref, "cells": a1s, "count": len(a1s)}


def pretty_sql(sql: str, params: list) -> str:
    """The executed statement with parameters filled in, FOR DISPLAY ONLY.

    What actually runs is the parameterised form — values never enter the SQL string (D30). A wall
    of `?` is unreadable over someone's shoulder, so this shows what the query meant without
    changing what was run.
    """
    out = sql
    for prm in params:
        out = out.replace("?", repr(prm), 1)
    out = re.sub(r"\s+", " ", out).strip()
    for kw in CLAUSES:
        out = out.replace(f" {kw} ", f"\n{kw} ")
    return out.replace(" AND ", "\n  AND ")


def suggestions(catalog) -> list[str]:
    """Starter questions built from THIS workbook's own labels.

    The four examples on the old landing page were about Gujarat, Bihar and diesel. On any other
    file they are not merely useless — they teach the user that the system understands a schema it
    has never seen, and the first click proves it wrong.

    The last one is deliberately unanswerable when the workbook declares something absent: showing
    that refusal is the point of the demo, and a suggestion is the only way a stranger discovers it.
    """
    ent, per = catalog.entity, catalog.period
    e = (catalog.labels_for(ent) or [None])[0]
    periods = catalog.labels_for(per) or []
    p = periods[-1] if periods else None

    def clean(x):
        return " ".join(str(x).split())          # labels can be multi-line (Hindi + English)

    # A record workbook has no period; its questions are about entities and their measures.
    measures = [c.name for c in catalog.columns
                if c.dtype in ("DOUBLE", "BIGINT") and c.name != "row_kind"]
    if not p and measures:
        m = measures[0]
        out = [f"Which {ent} has the highest {clean(m)}?"]
        if e:
            out.append(f"What is {clean(e)}'s {clean(m)}?")
        if len(measures) > 1:
            out.append(f"Which {ent} has the highest {clean(measures[1])}?")
        return out[:4]

    out = []
    if e and p:
        out.append(f"What was {clean(e)} in {clean(p)}?")
        out.append(f"Which {ent} was highest in {clean(p)}?")
        out.append(f"What share of the total was {clean(e)} in {clean(p)}?")
    elif p:
        out.append(f"Which {ent} was highest in {clean(p)}?")
    if catalog.absent and p:
        out.append(f"How much {catalog.absent[0]} was there in {clean(p)}?")
    return out[:4]
