"""Conversational memory: carry the previous plan forward, with a receipt for every slot.

The reframe this module exists for (D56): our gate used to ask "is this filter a word in the
question?". A follow-up inherits filters the question never contains, so that check would refuse
every follow-up. It now asks "does this filter have an AUTHORISED SOURCE?" — three are legal:

    stated     grounded in this turn's words          (the original lexical check, unchanged)
    inherited  carried from a recent turn's plan      (must match that plan exactly)
    default    a compiler default (D9, D24)           (must match a registered value)

Anything else is still an invention and still fails. The gate does not get looser; it gains a
second, equally mechanical check.

Window is 2 turns. That is not a compromise — an ablation found a 2-turn window captured almost
all the benefit (0% -> 74-86% accuracy by turn 3) while deeper memory swung between +14 and -16
points. Longer memory mostly imports stale context.

Session-scoped only. Persisting across sessions produces "confidently wrong queries keyed to the
wrong entity", which is the documented failure mode everywhere it has been tried.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog import Catalog                       # noqa: E402
from plan import Plan                            # noqa: E402

WINDOW = 2

STATED, INHERITED, DEFAULT, INVENTED = "stated", "inherited", "default", "invented"


@dataclass
class Turn:
    question: str
    plan: Plan


@dataclass
class Memory:
    turns: list[Turn] = field(default_factory=list)
    # Bumped only on clear(). A UI cache key must change when the conversation is RESET, but
    # must NOT change merely because a turn was recorded — otherwise answering a question
    # invalidates the key that was just set for it, and the question runs twice.
    epoch: int = 0

    def remember(self, question: str, plan: Plan) -> None:
        """Only answerable plans are worth inheriting from. A refusal or a clarification
        carries no filters, and remembering it would let a stale turn leak past the window."""
        if getattr(plan, "kind", None) in ("query", "derived"):
            self.turns.append(Turn(question, plan))
            del self.turns[:-WINDOW]

    def clear(self) -> None:
        self.turns.clear()
        self.epoch += 1

    @property
    def previous(self) -> Plan | None:
        return self.turns[-1].plan if self.turns else None

    def context_for_prompt(self) -> str:
        """What the planner sees. The previous PLAN, not just the previous question — the plan
        is unambiguous, and it is the artifact we can later check inheritance against."""
        if not self.turns:
            return ""
        lines = ["Recent turns, for resolving follow-ups like \"what about X?\":"]
        for t in self.turns:
            lines.append(f'  Question: {t.question}')
            lines.append(f'  Plan: {t.plan.model_dump_json()}')
        lines.append(
            "If this question is a follow-up, copy the previous plan and change only what the "
            "question changes. If it is a new, self-contained question, ignore the above."
        )
        return "\n".join(lines)


def _filters_of(plan) -> list:
    if getattr(plan, "kind", None) == "query":
        return list(plan.filters)
    if getattr(plan, "kind", None) == "derived":
        return [f for m in plan.measures for f in m.filters]
    return []


def provenance(question: str, plan: Plan, previous: Plan | None,
               catalog: Catalog, named) -> dict[str, str]:
    """Where each filter came from, one entry per column.

    Precedence is deliberate and load-bearing: **stated beats inherited**. If this turn names a
    value, it wins — so "which region used the most petrol?" after a diesel question cannot
    silently keep diesel. That is not a prompt instruction, it is the order these branches run in.
    """
    prev = {f.column: f.value for f in _filters_of(previous)} if previous else {}
    cols = {c.name: c for c in catalog.columns}
    out: dict[str, str] = {}
    for f in _filters_of(plan):
        col = cols.get(f.column)
        aliases = [a for a, v in (col.value_aliases if col else {}).items() if v == f.value]
        if named(f.value, catalog.labels_for(f.column) or [f.value], aliases):
            out[f.column] = STATED
        elif catalog.defaults.get(f.column) == f.value:
            out[f.column] = DEFAULT
        elif prev.get(f.column) == f.value:
            out[f.column] = INHERITED
        else:
            out[f.column] = INVENTED
    return out
