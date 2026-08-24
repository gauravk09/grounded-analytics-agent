"""The front door: question in, Answer out.

Two gates run BEFORE the planner, because both are facts about the question against the
catalog rather than judgments for a model:

  absent concept — the catalog declares it has no tax/revenue/population. A set lookup
                   cannot get this wrong; a 3B model demonstrably does.
  ambiguity      — "Uttar" matches UTTAR PRADESH and UTTARAKHAND. Ask (D20). Checked here
                   because a planner either resolves it invisibly or abstains, and either
                   way we would never see it afterwards.

Running them first is also cheaper: neither costs an API call.
"""

from __future__ import annotations

import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from answer import Answer, compose_refusal          # noqa: E402
from catalog import Catalog                         # noqa: E402
from execute import execute                         # noqa: E402
from sql_path import answer_via_sql                 # noqa: E402
from memory import Memory                           # noqa: E402
from trace import record                             # noqa: E402
from verify import (find_absent_concept, find_ambiguity,   # noqa: E402
                    find_unanchored_period, find_unsupported)


def ask(question: str, catalog: Catalog, db_path: Path, planner,
        memory: Memory | None = None) -> Answer:
    t0 = time.perf_counter()

    def done(answer: Answer, plan=None) -> Answer:
        record(question, answer, planner=planner, plan=plan,
               ms=int((time.perf_counter() - t0) * 1000))
        return answer

    absent = find_absent_concept(question, catalog)
    if absent:
        return done(Answer(question=question, status="abstained",
                           abstain_reason=compose_refusal("no_such_column", absent, catalog)))

    unsupported = find_unsupported(question)
    if unsupported:
        # The typed system can't express this (a ratio, a growth rate). Before refusing, try the
        # SQL escape hatch: a frontier model writes SQL, we validate it, and answer ONLY if the
        # number traces to cells. If it can't, the honest abstention stands.
        esc = answer_via_sql(question, catalog, db_path, planner)
        if esc is not None:
            return done(esc)
        return done(Answer(question=question, status="abstained",
                           abstain_reason=compose_refusal("unsupported_operation",
                                                          unsupported, catalog)))

    anchor = find_unanchored_period(question, catalog)
    if anchor:
        col, labels = anchor
        return done(Answer(question=question, status="clarify",
                           clarification=f'"last {col}" relative to what? The file runs to '
                                         f'{labels[-1]}. Which {col} did you mean?',
                           scope_options=labels[-3:][::-1]))

    amb = find_ambiguity(question, catalog)
    if amb:
        return done(Answer(question=question, status="clarify",
                           clarification=" ".join(a.question() for a in amb)))

    plan = planner.plan(question, catalog, memory)
    # The model guessed a categorical value we can't verify (product=HSD for "diesel") — ask which,
    # rather than refuse. Picking one re-asks with the value spelled out, and the plan then passes
    # (D98). This is the map/ask/abstain rule: when in doubt, ask the human the specific thing.
    if getattr(plan, "kind", None) == "abstain" and \
            getattr(plan, "reason_code", None) == "ambiguous_value":
        col = plan.detail
        labels = catalog.labels_for(col) or []
        return done(Answer(question=question, status="clarify",
                           clarification=f'Which {col} did you mean?',
                           scope_options=labels[:6]), plan)
    if getattr(plan, "kind", None) == "abstain" and \
            getattr(plan, "reason_code", None) in ("unsupported_operation", "planner_failed"):
        esc = answer_via_sql(question, catalog, db_path, planner)
        if esc is not None:
            return done(esc, plan)
    answer = execute(plan, catalog, db_path, question, memory)
    # Only remember turns that produced an answer. A refusal or a clarification carries no
    # filters, and remembering one would let a stale turn leak past the 2-turn window.
    if memory is not None and answer.status == "answered":
        memory.remember(question, plan)
    return done(answer, plan)


if __name__ == "__main__":
    from catalog import build
    from planner import get_planner

    root = Path(__file__).resolve().parent.parent
    from workbook import load
    _spec, db, _c = load()
    cat, planner = build(db), get_planner()

    for q in sys.argv[1:]:
        a = ask(q, cat, db, planner)
        print(f"\nQ: {q}")
        print(f"   [{a.status}] {a.text()}")
        if a.echo:
            print(f"   computed as: {a.echo}")
        def show(name, v, indent="   "):
            cells = f"{len(v.citations)} cell(s): " + ", ".join(str(c) for c in v.citations[:4])
            if len(v.citations) > 4:
                cells += " ..."
            head = f"{indent}{name} = {v.formatted}"
            print(f"{head}  <- {cells}" if v.citations else
                  f"{head}  ({v.derivation}) — computed, cites nothing directly")
            for pn, part in v.parts.items():
                show(pn, part, indent + "     ")

        for name, v in a.slots.items():
            show(name, v)
