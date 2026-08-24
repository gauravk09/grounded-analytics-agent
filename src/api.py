"""HTTP surface. Serves Answers and Specs as JSON; computes nothing itself.

The same contract the Streamlit renderer has, moved behind HTTP: **join and draw, never compute**.
Concretely, `text` is composed HERE and shipped whole. The tempting alternative — ship
`narration` ("{v1} thousand tonnes") plus `slots`, and let React join them — would make the browser
a second place numbers are made, which is exactly the property that makes a hallucinated figure
unrepresentable rather than merely unlikely. The client receives finished sentences and evidence.

Statefulness: conversation memory lives server-side, keyed by a session string the client sends.
`Turn` holds a Plan object, and round-tripping plans through a browser would put the
"stated beats inherited" precedence rules on the wrong side of the wire.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import asdict, is_dataclass
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from answer import Answer                                       # noqa: E402
from ask import ask                                             # noqa: E402
from catalog import build                                       # noqa: E402
from ingest_spec import ingest                                  # noqa: E402
from memory import Memory                                       # noqa: E402
from planner import DEFAULT_PROVIDER, PROVIDERS, list_models, make_planner   # noqa: E402
from present import pretty_sql, suggestions, cell_ranges         # noqa: E402
from probe import profile                                       # noqa: E402
from propose import propose                                     # noqa: E402
from spec import Spec                                           # noqa: E402
from xlsx_io import load as load_workbook                       # noqa: E402
from render.deck_agent import story_deck                        # noqa: E402

DATA, SPECS = ROOT / "data", ROOT / "specs"

app = FastAPI(title="Grounded Spreadsheet Q&A")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])

SESSIONS: dict[str, Memory] = {}


# ── workbook registry ───────────────────────────────────────────────────────────
def specs() -> dict[str, Spec]:
    """A workbook is a confirmed spec plus the database it produced. No spec, no workbook —
    an un-confirmed file is deliberately unaskable (D59)."""
    out = {}
    for p in sorted(SPECS.glob("*.yaml")):
        try:
            out[p.stem] = Spec.load(p)
        except Exception:
            continue        # a half-written spec must not take the whole registry down
    return out


def resolve(workbook: str) -> tuple[Spec, Path]:
    s = specs().get(workbook)
    if s is None:
        raise HTTPException(404, f"no workbook {workbook!r}")
    db = DATA / f"{Path(s.file).stem}.duckdb"
    if not db.exists():
        raise HTTPException(409, f"{workbook!r} has a spec but was never ingested")
    return s, db


@lru_cache(maxsize=8)
def catalog_for(workbook: str, mtime: float):
    """Cached per workbook. `mtime` is in the key so re-ingesting invalidates it — and is a
    parameter rather than read inside, because a cache key must not depend on state the cached
    call could change (D57)."""
    s, db = resolve(workbook)
    # Passed verbatim — NOT `or None`. `or None` makes an empty list fall through to catalog.py's
    # PPAC defaults, which put ['tax', 'revenue', 'gdp', ...] on the Union Budget and hard-refused
    # every question the Budget is actually about. Same bug as D58, arriving through the fallback
    # instead of through the model. A confirmed spec's empty list means EMPTY.
    # `spec=` — not annotations/absent as loose arguments. Passing only those two left `entity`,
    # `period`, `measure` and `defaults` at their fallbacks, so the API silently ran without
    # `product = ALL`: the fuel file's three sheets overlap, and summing them triples every
    # figure. The measured naive total is 688,573 against a true 223,480.
    return build(db, s.table, spec=s)


def catalog(workbook: str):
    _, db = resolve(workbook)
    return catalog_for(workbook, db.stat().st_mtime)


# ── serialisation ───────────────────────────────────────────────────────────────
def value_json(name: str, v) -> dict:
    return {
        "name": name,
        "formatted": v.formatted,
        "unit": v.unit,
        "derivation": v.derivation,
        "sql": pretty_sql(v.sql, v.params) if v.sql else None,
        "citations": [asdict(c) for c in v.citations],
        "ranges": cell_ranges(v.citations),
        "parts": [value_json(n, p) for n, p in (v.parts or {}).items()],
    }


def answer_json(a: Answer) -> dict:
    return {
        "question": a.question,
        "status": a.status,
        "text": a.text(),                       # composed HERE — see module docstring
        "echo": a.echo,
        "sql": pretty_sql(a.sql, a.params) if a.sql else None,
        "scope_options": a.scope_options,
        "slots": [value_json(n, v) for n, v in a.slots.items()],
        "citation_count": len(a.all_citations()),
    }


# ── requests ────────────────────────────────────────────────────────────────────
class Model(BaseModel):
    provider: str | None = DEFAULT_PROVIDER
    model: str | None = None
    api_key: str | None = None
    local_first: bool = False


class AskIn(Model):
    question: str
    workbook: str
    session: str = "default"


class DeckIn(Model):
    workbook: str
    goal: str = ""


class ProposeIn(Model):
    file: str
    use_model: bool = True


class ConfirmIn(BaseModel):
    spec: dict


def planner_from(req: Model):
    """Build the cascade from what the caller actually supplied.

    A provider with no key must be dropped, not passed along: the paid tier raises at construction
    when `allow_env=False`, which would turn "run the free local model" into a 400. The key is never
    read from the server's environment — what runs is what the caller sent (D40).

    EXCEPTION, opt-in only: if ALLOW_ENV_KEY=1 is set in the server environment (a local test
    convenience, never the default), a keyless request may fall back to the env key. Guarded so it
    can never silently ship — D40's rule holds unless a human explicitly turns this on.
    """
    allow_env = os.environ.get("ALLOW_ENV_KEY") == "1"
    provider = req.provider if (req.api_key or allow_env) else None
    try:
        return make_planner(provider, req.model, req.api_key or None,
                            req.local_first, allow_env=allow_env)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


# ── endpoints ───────────────────────────────────────────────────────────────────
@app.get("/api/providers")
def providers():
    return {"default": DEFAULT_PROVIDER,
            "providers": {p: {"env": c["env"], "models": list_models(p, None, allow_env=False)}
                          for p, c in PROVIDERS.items()}}


@app.get("/api/workbooks")
def workbooks():
    out = []
    for wid, s in specs().items():
        db = DATA / f"{Path(s.file).stem}.duckdb"
        out.append({"id": wid, "file": s.file, "table": s.table, "entity": s.entity,
                    "period": s.period, "measure": s.measure, "unit": s.unit,
                    "sheets": [sh.name for sh in s.sheets], "ingested": db.exists()})
    return out


@app.get("/api/workbooks/{workbook}")
def workbook(workbook: str):
    s, _ = resolve(workbook)
    c = catalog(workbook)
    return {"spec": s.model_dump(exclude_none=True),
            "notes": [{"sheet": sh, "a1": a1, "text": t} for sh, a1, t in c.notes],
            "columns": [{"name": col.name, "description": col.description,
                         "labels": col.labels, "aliases": col.aliases} for col in c.columns],
            "absent": c.absent,
            # Generated from this file's labels — a hardcoded starter question is a claim about
            # a schema, and on the wrong workbook it is a false one.
            "suggestions": suggestions(c),
            # Surfaced, not swallowed: an annotation naming no column silently removes aliases.
            "unknown_annotations": c.unknown_annotations}


@app.delete("/api/workbooks/{workbook}")
def delete_workbook(workbook: str):
    """Remove a source entirely — its confirmed spec, the database it produced, and the uploaded
    file. Destructive and irreversible, so the client confirms first. Caches and this workbook's
    conversation memory are cleared so nothing stale survives the delete."""
    s = specs().get(workbook)
    if s is None:
        raise HTTPException(404, f"no workbook {workbook!r}")
    db = DATA / f"{Path(s.file).stem}.duckdb"
    targets = [SPECS / f"{workbook}.yaml", db, Path(str(db) + ".wal"), DATA / Path(s.file).name]
    removed = []
    for p in targets:
        if p.exists():
            p.unlink()
            removed.append(p.name)
    catalog_for.cache_clear()
    for k in [k for k in SESSIONS if k.endswith(f":{workbook}")]:
        SESSIONS.pop(k, None)
    return {"deleted": workbook, "removed": removed}


@app.get("/api/files")
def uploaded_files():
    exts = ("*.xlsx", "*.xlsm", "*.csv", "*.tsv")
    return sorted(p.name for e in exts for p in DATA.glob(e) if not p.name.startswith("~"))


@app.post("/api/upload")
async def upload(f: UploadFile = File(...)):
    if not f.filename.lower().endswith((".xlsx", ".xlsm", ".csv", ".tsv")):
        raise HTTPException(415, "only .xlsx / .xlsm / .csv / .tsv")
    dest = DATA / Path(f.filename).name
    with dest.open("wb") as out:
        shutil.copyfileobj(f.file, out)
    return {"file": dest.name}


@app.post("/api/propose")
def propose_spec(req: ProposeIn):
    path = DATA / Path(req.file).name
    if not path.exists():
        raise HTTPException(404, f"no file {req.file!r}")
    spec = propose(path, planner_from(req) if req.use_model else None)
    return {
        "spec": spec.model_dump(exclude_none=True),
        # Structure is SHOWN, never asked (D62) — so the grid travels with the proposal.
        "grids": {sh.name: profile(path, sh.name).grid(rows=40, cols=14) for sh in spec.sheets},
        "examples": {sh.name: next((r.label for r in profile(path, sh.name).rows
                                    if r.looks_section), None) for sh in spec.sheets},
    }


@app.post("/api/confirm")
def confirm(req: ConfirmIn):
    try:
        spec = Spec.model_validate(req.spec)
    except ValidationError as e:
        # A rejected spec (e.g. an unsafe table name) is the client's fault, not a server crash —
        # return 400 with the reason, never a 500 stack trace that could leak internals.
        raise HTTPException(400, f"invalid spec: {e.errors()[0]['msg']}")

    # Normalize sheet constants and derive a default for a multi-sheet constant dimension (D96).
    # Two bugs hid here: (1) a constant typed as "product = ALL" arrived with stray spaces, so the
    # column became "product " and never matched; (2) with no default, a question that doesn't name
    # the product forces the model to add a `product=ALL` filter, which the overfilter gate then
    # rejects as "invented" — the file looked unanswerable when it wasn't. Trimming and defaulting
    # to the 'all'-like variant fixes both mechanically, so it can't depend on the client getting
    # spacing or a hidden field right.
    values_by_key: dict[str, set] = {}
    for sh in spec.sheets:
        sh.constants = {k.strip(): v.strip() for k, v in sh.constants.items()}
        for k, v in sh.constants.items():
            values_by_key.setdefault(k, set()).add(v)
    for k, vals in values_by_key.items():
        allish = [v for v in vals if v.lower() in ("all", "all products", "total")]
        if k not in spec.defaults and len(vals) > 1 and allish:
            spec.defaults[k] = allish[0]

    path = DATA / Path(spec.file).name
    if not path.exists():
        raise HTTPException(404, f"no file {spec.file!r}")
    # Ingest FIRST — it can refuse an ambiguous spec (the double-count guard, D97). Only save the
    # spec once the data actually ingested, so a rejected confirm leaves no half-created workbook.
    try:
        counts = ingest(spec, path, DATA / f"{Path(spec.file).stem}.duckdb")
    except ValueError as e:
        raise HTTPException(400, str(e))
    out = SPECS / f"{Path(spec.file).stem}.yaml"
    spec.save(out)
    catalog_for.cache_clear()
    return {"workbook": out.stem, "counts": counts}


@app.post("/api/ask")
def ask_question(req: AskIn):
    _, db = resolve(req.workbook)
    mem = SESSIONS.setdefault(f"{req.session}:{req.workbook}", Memory())
    return answer_json(ask(req.question, catalog(req.workbook), db, planner_from(req), mem))


@app.get("/api/sheet")
def sheet_cells(sheet: str, workbook: str | None = None, file: str | None = None):
    """The raw cells of one sheet, so the client can render the grid and highlight a cited cell —
    letting a reviewer SEE the source in context, not just read its address. Works for a confirmed
    `workbook` (answer citations) OR a raw uploaded `file` (onboarding, before ingest). Values and
    formulas only; no computation, bounded so a huge sheet cannot blow up the response."""
    if workbook:
        s, _ = resolve(workbook)
        path = DATA / Path(s.file).name      # basename only — never escape DATA
    elif file:
        path = DATA / Path(file).name        # basename only — never escape DATA
    else:
        raise HTTPException(422, "pass workbook or file")
    if not path.exists():
        raise HTTPException(404, "missing file")
    wbv = load_workbook(path, data_only=True)
    wbf = load_workbook(path, data_only=False)
    if sheet not in wbv.sheetnames:
        raise HTTPException(404, f"no sheet {sheet!r} in {workbook!r}")
    wv, wf = wbv[sheet], wbf[sheet]
    max_row = min(wv.max_row or 1, 2000)
    cells = []
    for r in range(1, max_row + 1):
        for c in range(1, min(wv.max_column or 1, 60) + 1):
            v = wv.cell(r, c).value
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            fx = wf.cell(r, c).value
            cells.append({"a1": wv.cell(r, c).coordinate, "r": r, "c": c, "v": str(v),
                          "f": fx if isinstance(fx, str) and fx.startswith("=") else None})
    return {"sheet": sheet, "max_row": max_row, "max_col": min(wv.max_column or 1, 60),
            "cells": cells}


@app.post("/api/deck")
def make_deck(req: DeckIn):
    """Author a presentation FROM the workbook: mine cited findings, let the agent arrange them
    into a story, render a .pptx AND return the slides as JSON so the client can show the deck
    in-window (each slide's number stays welded to its source cells). The .pptx is served
    separately by GET /api/deck/{workbook}/file for download — same contract as /api/ask: the
    client never sees a raw figure, only finished text and its evidence."""
    _, db = resolve(req.workbook)
    out = ROOT / "output" / f"{req.workbook}_story.pptx"
    # The deck needs no planner: findings are mined deterministically (cited), and the narrative
    # arrangement falls back to the workbook's own order when no model is reachable. So a missing
    # key must NOT 400 here the way it does for /api/ask.
    try:
        pl = planner_from(req)
    except HTTPException:
        pl = None
    _, ordered, plan = story_deck(catalog(req.workbook), db, pl, out, goal=req.goal)
    slides = [{"text": f.text(),
               "cells": [{"sheet": c.sheet, "a1": c.a1} for c in f.all_citations()]}
              for f in ordered]
    return {"title": plan["title"], "subtitle": plan["subtitle"],
            "closing": plan.get("closing", ""), "slides": slides,
            "pptx": f"/api/deck/{req.workbook}/file"}


@app.get("/api/deck/{workbook}/file")
def deck_file(workbook: str):
    """Download the .pptx built by the most recent POST /api/deck for this workbook."""
    resolve(workbook)                                 # 404s on an unknown workbook
    out = ROOT / "output" / f"{Path(workbook).name}_story.pptx"
    if not out.exists():
        raise HTTPException(404, "no deck built yet — generate it first")
    return FileResponse(
        out, filename=f"{workbook}.pptx",
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


@app.post("/api/session/{session}/clear")
def clear_session(session: str):
    # Switching workbook must not carry context across: inheriting "for Bihar" into a different
    # file is meaningless, so sessions are keyed by workbook and cleared per workbook (D62).
    for k in [k for k in SESSIONS if k.startswith(f"{session}:")]:
        SESSIONS[k].clear()
    return {"cleared": session}
