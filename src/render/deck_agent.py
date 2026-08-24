"""Agentic deck authoring: the model plans the STORY, the pipeline supplies every NUMBER.

The division is the same one the whole project rests on. The agent decides what to say and in what
order — a narrative arc of questions — and it emits ENGLISH ONLY, never a digit. Each question is
answered by `ask()`, which returns a number welded to its source cells or a refusal. So a deck can
be authored by a model and still have the property that every figure on every slide is traceable,
and any claim the data cannot support becomes an abstain slide rather than a confident fiction.

    narrative()   model (or a deterministic fallback) -> a list of questions = the story
    ask()         each question -> a cited Answer (unchanged pipeline)
    build_deck()  Answers -> a designed .pptx (unchanged renderer)

If no model is available, the story falls back to the workbook's own generated suggestions — still
generic, just not curated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import duckdb

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ask import ask                                   # noqa: E402
from plan import QueryPlan, Metric, Filter, DerivedPlan, Measure, Derivation   # noqa: E402
from compile import compile_plan                     # noqa: E402
from execute import _cells                           # noqa: E402
from answer import Answer, Citation                  # noqa: E402
from execute import execute                          # noqa: E402
from catalog import Catalog                           # noqa: E402
from present import suggestions                       # noqa: E402
from render.deck import build_deck                    # noqa: E402

NARRATIVE_PROMPT = """You are planning a short data presentation. You are given the COLUMNS of a
dataset (names only, never values). Propose a narrative: a title, a one-line subtitle, and 3-5
questions that build a story a reader would find useful. Ask ONLY questions these columns can
answer. Never invent a number; you are choosing what to ask, not answering.

Return JSON only:
{"title": "...", "subtitle": "...", "questions": ["...", "..."]}"""


def _columns_blurb(catalog: Catalog) -> str:
    return "Columns: " + ", ".join(
        f"{c.name}" + (f" ({', '.join(c.labels[:6])}…)" if c.labels else "")
        for c in catalog.columns if c.name != "row_kind")


@dataclass
class Leaderboard:
    """A top-N finding: several cited rows, the basis for a ranked list or a bar chart. Quacks like
    an Answer (status/text/all_citations) so the story and deck treat findings uniformly."""
    question: str
    measure: str
    rows: list = field(default_factory=list)          # (entity, formatted, raw, [Citation])
    status: str = "answered"

    def text(self) -> str:
        head = ", ".join(f"{e} ({f})" for e, f, _, _ in self.rows[:3])
        return f"Top {len(self.rows)} by {self.measure}: {head}"

    def all_citations(self) -> list:
        return [c for *_rest, cs in self.rows for c in cs]


@dataclass
class Trend:
    """A time series: one value per period, in chronological order. Rendered as a line chart, and
    the headline is the direction of travel — the specific-to-this-sheet story of what changed."""
    question: str
    measure: str
    rows: list = field(default_factory=list)          # (period, formatted, raw, [Citation])
    headline: str = ""
    status: str = "answered"

    def text(self) -> str:
        return self.headline or f"{self.measure} over {len(self.rows)} periods"

    def all_citations(self) -> list:
        return [c for *_rest, cs in self.rows for c in cs]


def _fmt(x) -> str:
    return f"{x:,.2f}" if isinstance(x, (int, float)) else str(x)


def leaderboard(catalog: Catalog, db_path: Path, measure: str, n: int = 5,
                period: str | None = None, latest: str | None = None) -> Leaderboard | None:
    """Top-N entities by a measure, each row cited via the compiler's own provenance query."""
    ent = catalog.entity
    filters = [Filter(column=period, op="=", value=latest)] if period and latest else []
    plan = QueryPlan(kind="query", metric=Metric(agg="sum", column=measure), filters=filters,
                     group_by=[ent], sort="desc", limit=n, narration="{v1} {v2}")
    try:
        c = compile_plan(plan, catalog)
    except Exception:
        return None
    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(c.value_sql, c.params).fetchall()      # (entity, value) x n
    out = []
    for entity, value in rows:
        prov_sql, prov_params = c.provenance_sql({ent: entity})
        ids = [r[0] for r in con.execute(prov_sql, prov_params).fetchall()]
        out.append((str(entity), _fmt(value), value, _cells(con, ids, c.metric_column)))
    con.close()
    return Leaderboard(question=f"Top {n} {ent}s by {measure}"
                       + (f" in {latest}" if latest else ""), measure=measure, rows=out) if out else None


def growth(catalog: Catalog, db_path: Path, entity_value: str, measure: str,
           period: str, first: str, last: str) -> Answer | None:
    """Percent change for one entity between two periods — a cited DerivedPlan (lineage tree)."""
    def m(name, per):
        return Measure(name=name, agg="sum", column=measure,
                       filters=[Filter(column=catalog.entity, op="=", value=entity_value),
                                Filter(column=period, op="=", value=per)])
    plan = DerivedPlan(kind="derived", measures=[m("m1", last), m("m2", first)],
                       derive=Derivation(op="percent_change", of="m1", by="m2", scale=100),
                       narration=f"{entity_value} changed {{v1}}% on {measure} "
                                 f"from {first} to {last}.")
    from execute import execute as _exec
    try:
        a = _exec(plan, catalog, db_path, question=f"how did {entity_value} change from {first} to {last}")
        return a if a.status == "answered" else None
    except Exception:
        return None


def trend(catalog: Catalog, db_path: Path, measure: str, period: str) -> "Trend | None":
    """Total the measure per period across the whole span — the trend line. Each period's total is
    cited via the compiler's provenance query, so the line rests on real cells."""
    labels = catalog.labels_for(period)
    if len(labels) < 3:
        return None
    plan = QueryPlan(kind="query", metric=Metric(agg="sum", column=measure), filters=[],
                     group_by=[period], sort="none", limit=0, narration="{v1} {v2}")
    try:
        c = compile_plan(plan, catalog)
    except Exception:
        return None
    con = duckdb.connect(str(db_path), read_only=True)
    got = {str(k): v for k, v in con.execute(c.value_sql, c.params).fetchall()}
    rows = []
    for lab in labels:                               # chronological, by the catalog's own order
        if lab not in got:
            continue
        prov_sql, prov_params = c.provenance_sql({period: lab})
        ids = [r[0] for r in con.execute(prov_sql, prov_params).fetchall()]
        rows.append((lab, _fmt(got[lab]), got[lab], _cells(con, ids, c.metric_column)))
    con.close()
    if len(rows) < 3:
        return None
    first, last = rows[0][2], rows[-1][2]
    pct = (last - first) / first * 100 if first else 0
    direction = "risen" if pct > 0 else "fallen"
    head = (f"Total {measure} has {direction} {abs(pct):,.0f}% over {rows[0][0]}–{rows[-1][0]}, "
            f"from {rows[0][1]} to {rows[-1][1]}.")
    return Trend(question=f"How has {measure} moved over time?", measure=measure,
                 rows=rows, headline=head)


def share(catalog: Catalog, db_path: Path, entity_value: str, measure: str,
          period: str | None, latest: str | None) -> "Answer | None":
    """One entity as a percent of the whole — a concentration insight specific to this sheet."""
    ent = catalog.entity
    base = [Filter(column=period, op="=", value=latest)] if period and latest else []
    top = Measure(name="m1", agg="sum", column=measure,
                  filters=base + [Filter(column=ent, op="=", value=entity_value)])
    whole = Measure(name="m2", agg="sum", column=measure, filters=list(base))
    plan = DerivedPlan(kind="derived", measures=[top, whole],
                       derive=Derivation(op="divide", of="m1", by="m2", scale=100),
                       narration=f"{entity_value} alone is {{v1}}% of the total"
                                 + (f" in {latest}" if latest else "") + ".")
    from execute import execute as _exec
    try:
        a = _exec(plan, catalog, db_path, question=f"what share is {entity_value}")
        return a if a.status == "answered" else None
    except Exception:
        return None


def survey(catalog: Catalog, db_path: Path) -> list[Answer]:
    """Mine the sheet for CITED findings, deterministically — no model, no invented numbers.

    Every finding is produced by the normal pipeline, so it arrives already welded to its source
    cells. The agent later arranges these into a story; it never sees a raw value it could
    hallucinate from, only facts the data already backs. This is what "use the sheet to make a
    story" means without giving up the no-hallucination guarantee: the sheet supplies the facts,
    the model supplies the order and the prose.
    """
    ent = catalog.entity
    if ent not in catalog.column_names:
        return []
    measures = [c.name for c in catalog.columns
                if c.dtype in ("DOUBLE", "BIGINT") and not c.internal]
    period = catalog.period if catalog.labels_for(catalog.period) else None
    latest = catalog.labels_for(period)[-1] if period else None

    findings: list[Answer] = []
    # How many entities — the scene-setter. Only when there is one row per entity (no period),
    # else count(*) sums across periods and answers a different question.
    if not period:
        cnt = QueryPlan(kind="query", metric=Metric(agg="count", column=ent), filters=[],
                        group_by=[], sort="none", limit=0,
                        narration=f"The file tracks {{v1}} {ent}s.")
        a = execute(cnt, catalog, db_path, question=f"how many {ent}s are there")
        if a.status == "answered":
            findings.append(a)

    if not measures:
        return findings
    primary = measures[0]
    plabels = catalog.labels_for(period) if period else []

    # 1. THE TREND — the whole story in one line and a chart. Leads the deck when there is time.
    if period:
        t = trend(catalog, db_path, primary, period)
        if t:
            findings.append(t)

    # 2. THE LEADERBOARD — who is on top, as a bar chart.
    lb = leaderboard(catalog, db_path, primary, 5, period, latest)
    if lb:
        findings.append(lb)

    # 3. CONCENTRATION — the top entity's share of the whole. A specific, non-obvious insight.
    if lb:
        sh = share(catalog, db_path, lb.rows[0][0], primary, period, latest)
        if sh:
            findings.append(sh)

    # 4. GROWTH — how the leader moved across the full span.
    if lb and len(plabels) >= 2:
        g = growth(catalog, db_path, lb.rows[0][0], primary, period, plabels[0], plabels[-1])
        if g:
            findings.append(g)

    # 5. SECONDARY MEASURES — a leader each, so a multi-metric record file still tells a full story.
    for m in measures[1:]:
        filters = [Filter(column=period, op="=", value=latest)] if period else []
        plan = QueryPlan(kind="query", metric=Metric(agg="sum", column=m), filters=filters,
                         group_by=[ent], sort="desc", limit=1,
                         narration=f"{{v1}} leads on {m}" + (f" in {latest}" if latest else "")
                                   + ", at {v2}.")
        a = execute(plan, catalog, db_path,
                    question=f"which {ent} has the highest {m}" + (f" in {latest}" if latest else ""))
        if a.status == "answered":
            findings.append(a)

    # scene-setter (entity count) belongs first when we have it
    return findings


def narrative(catalog: Catalog, goal: str = "", planner=None) -> dict:
    """A title/subtitle/questions plan. Model if one is reachable, else the workbook's suggestions."""
    blurb = _columns_blurb(catalog)
    if planner is not None:
        try:
            plan = planner.chat_json(NARRATIVE_PROMPT, f"{blurb}\nGoal: {goal or 'overview'}") or {}
            qs = [q for q in plan.get("questions", []) if isinstance(q, str)][:5]
            if qs:
                return {"title": plan.get("title") or "Data analysis",
                        "subtitle": plan.get("subtitle") or "", "questions": qs}
        except Exception:
            pass
    # Deterministic fallback: the workbook describes its own starter questions.
    return {"title": "Data analysis",
            "subtitle": "Auto-generated from the dataset's own fields",
            "questions": suggestions(catalog) or []}




STORY_PROMPT = """You are the editor of a short data story. You are given a numbered list of
FINDINGS already computed from a dataset (each is a factual sentence with real numbers you must not
change). Arrange them into a narrative and frame it. You choose ORDER and write connective prose —
you never alter a number and never add a fact not in the list.

Return JSON only:
{"title": "...", "subtitle": "one-line thesis", "order": [list of finding indices, best first],
 "closing": "one-sentence takeaway"}"""


def story(findings: list[Answer], goal: str = "", planner=None) -> dict:
    """The agent arranges CITED findings into a narrative. Fallback: keep them in mined order."""
    # A deterministic thesis and closing, so the story stands even when no model is reachable.
    # The closing is SYNTHESIZED FROM THE CITED FINDINGS, never invented: if one entity tops
    # several measures, that pattern is itself a finding worth stating.
    leaders = [a for a in findings if " leads on " in a.text()]
    names = [a.text().split(" leads on ")[0] for a in leaders]
    closing = ""
    if len(names) >= 2 and len(set(names)) == 1:
        closing = f"{names[0]} leads on every measure in this file."
    elif leaders:
        closing = leaders[0].text()
    default = {"title": (goal[:1].upper() + goal[1:]) if goal else "What the data shows",
               "subtitle": f"{len(findings)} findings, every number traced to its source cell",
               "order": list(range(len(findings))), "closing": closing}
    if not findings:
        return default
    if planner is None:
        return default
    listing = "\n".join(f"{i}. {a.text()}" for i, a in enumerate(findings))
    try:
        plan = planner.chat_json(STORY_PROMPT, f"Goal: {goal}\n\n{listing}") or {}
        order = [i for i in plan.get("order", []) if isinstance(i, int) and 0 <= i < len(findings)]
        seen: set = set()
        order = [i for i in order if not (i in seen or seen.add(i))]
        for i in range(len(findings)):          # never drop a finding silently
            if i not in seen:
                order.append(i)
        return {"title": plan.get("title") or default["title"],
                "subtitle": plan.get("subtitle") or default["subtitle"],
                "order": order, "closing": plan.get("closing") or default["closing"]}
    except Exception:
        return default


def story_deck(catalog: Catalog, db_path: Path, planner, path: Path,
               goal: str = "", memory=None) -> tuple[Path, list]:
    """Author a deck FROM THE SHEET: mine cited findings, let the agent arrange them into a story,
    render. The numbers come from the sheet; the narrative comes from the model. Returns (path,
    ordered findings)."""
    findings = survey(catalog, db_path)

    # A BRIEF turns the agent into a question-writer: it proposes questions shaped by the brief,
    # each answered through the cited pipeline (never a raw number), and the answered ones become
    # slides alongside the mined findings. No brief -> the generic data story. This is the
    # Coreworks shape: a prompt steers the narrative, the numbers stay traceable.
    if goal and planner is not None:
        for q in narrative(catalog, goal, planner)["questions"]:
            try:
                a = ask(q, catalog, db_path, planner, memory=memory)
                if a.status == "answered":
                    findings.append(a)
            except Exception:
                continue

    plan = story(findings, goal, planner)
    ordered = [findings[i] for i in plan["order"]]
    out = build_deck(ordered, path, title=plan["title"], subtitle=plan["subtitle"],
                     closing=plan.get("closing", ""))
    return out, ordered


if __name__ == "__main__":
    from spec import Spec
    from catalog import build
    from planner import get_planner
    root = Path(__file__).resolve().parent.parent.parent
    spec = Spec.load(root / "specs" / "BananaPatterns-Climbing-2026-08-18.yaml")
    db = root / "data" / "BananaPatterns-Climbing-2026-08-18.duckdb"
    cat = build(db, spec.table, spec=spec)
    out, answers = story_deck(cat, db, get_planner(),
                              root / "output" / "story_deck.pptx",
                              goal="which stocks are breaking out most strongly")
    print(f"wrote {out}")
    for a in answers:
        print(f"  [{a.status}] {a.question} -> {a.text()[:70]}")
