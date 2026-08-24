"""One JSON line per question, for debugging after the fact.

The parameterised statement, the raw plan and the tier that produced it are engineering
artifacts — useful when an answer looks wrong, noise in a user interface. They live here
instead of on screen.

Never logged: the API key, and anything else from the environment. Only the model name.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "logs" / "trace.jsonl"


def record(question: str, answer, *, planner=None, plan=None, ms: int | None = None) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    tiers = [type(t).__name__ for t in getattr(planner, "tiers", [])] if planner else []
    row = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": question,
        "status": answer.status,
        "text": answer.text(),
        "ms": ms,
        "tiers": tiers,
        "model": getattr(getattr(planner, "tiers", [None])[-1], "model", None) if tiers else None,
        "plan": plan.model_dump() if plan is not None else None,
        "sql": answer.sql,
        "params": answer.params,
        "queries": [{"of": n, "sql": q, "params": pr} for n, q, pr in answer.all_queries()],
        "echo": answer.echo,
        "slots": {k: {"raw": v.raw, "formatted": v.formatted,
                      "cells": [f"{c.sheet}!{c.a1}" for c in v.citations]}
                  for k, v in answer.slots.items()},
        "abstain_reason": answer.abstain_reason,
        "clarification": answer.clarification,
    }
    with LOG.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")
