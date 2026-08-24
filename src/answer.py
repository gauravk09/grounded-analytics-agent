"""The Answer: what every renderer consumes.

This is the seam that makes PPT and PDF additions rather than rewrites. The pipeline ends in
structured data, not a chat string — narration and numbers stay separate so a citation can
attach to a *value* rather than to a sentence. Flattening to text is a rendering choice, made
last, by whoever is drawing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


def compose_refusal(code: str, detail: str, catalog=None) -> str:
    """Write the refusal sentence from the catalog, never from the model's prose."""
    if code == "no_such_column":
        have = ", ".join(sorted(catalog.column_names)) if catalog else "the columns in this file"
        return (f"there is no {detail or 'such'} column in this file. It has: {have}"
                if detail else f"that concept is not in this file. It has: {have}")
    if code == "value_not_in_column" and catalog:
        labels = catalog.labels_for(detail)
        if labels:
            return f"'{detail}' in this file only contains: {', '.join(labels)}"
        return f"that value is not present in '{detail}'"
    if code == "not_a_data_question":
        return "that is not a question this table can answer"
    if code == "planner_failed":
        return ("I could not build a reliable query for that question. The data may well "
                "support it — this is a limit of the planner, not of the file. Try rephrasing, "
                "or naming the state, fuel and year explicitly")
    if code == "unsupported_operation":
        return (f"answering that needs {detail or 'an operation'}, which this system cannot "
                f"express yet — it computes one aggregate per question, not ratios between two. "
                f"Ask for the parts separately and they will each be traceable")
    if code == "empty_result":
        return "the query was valid but matched no rows in the file"
    return "the data does not support that question"


@dataclass
class Citation:
    sheet: str
    a1: str
    raw_value: str | None = None
    formula: str | None = None

    def __str__(self) -> str:
        s = f"{self.sheet}!{self.a1}"
        return f"{s} [{self.formula}]" if self.formula else s


@dataclass
class Value:
    raw: float | str
    formatted: str
    citations: list[Citation] = field(default_factory=list)
    unit: str | None = None
    # A derived number (a share, a growth rate) exists nowhere in the spreadsheet, so it has
    # no cells of its own. Its provenance is its inputs' provenance, which makes lineage a
    # tree rather than a flat list — and a better answer to "where did this come from?",
    # because it shows the arithmetic as well as the cells.
    parts: dict[str, "Value"] = field(default_factory=dict)
    derivation: str | None = None      # human-readable, e.g. "m1 / m2 x 100"
    # A derived answer runs one query per measure. Hanging the statement on the Value keeps
    # each number next to the query that produced it, instead of showing one query for an
    # answer that took two.
    sql: str | None = None
    params: list = field(default_factory=list)


@dataclass
class Answer:
    question: str
    status: Literal["answered", "abstained", "clarify"]
    narration: str = ""                       # template with {v1} holes — never pre-filled
    slots: dict[str, Value] = field(default_factory=dict)
    sql: str | None = None
    params: list = field(default_factory=list)
    echo: str | None = None                   # plan in plain English (D27)
    abstain_reason: str | None = None
    clarification: str | None = None          # the question to ask back (D20)
    scope_options: list[str] = field(default_factory=list)   # buttons for a scope question

    def text(self) -> str:
        """Join template and slots. The ONLY place a number becomes part of a sentence."""
        if self.status == "clarify":
            return self.clarification or "I need one clarification before I can answer."
        if self.status == "abstained":
            return f"I can't answer this from the file — {self.abstain_reason}"
        return self.narration.format(**{k: v.formatted for k, v in self.slots.items()})

    def all_queries(self) -> list[tuple[str, str, list]]:
        """Every statement actually run, in tree order. Used by the trace log."""
        out = []
        if self.sql:
            out.append(("main", self.sql, self.params))

        def walk(prefix: str, v: Value) -> None:
            if v.sql:
                out.append((prefix, v.sql, v.params))
            for name, part in v.parts.items():
                walk(name, part)

        for name, v in self.slots.items():
            walk(name, v)
        return out

    def all_citations(self) -> list[Citation]:
        """Flattened, including derived inputs — every cell this answer rests on."""
        def walk(v: Value) -> list[Citation]:
            return list(v.citations) + [c for p in v.parts.values() for c in walk(p)]
        return [c for v in self.slots.values() for c in walk(v)]
