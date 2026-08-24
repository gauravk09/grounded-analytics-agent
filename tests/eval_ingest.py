"""Round-trip scoreboard for ingest. Evals-first: this exists BEFORE the role-classifier, so the
work has a red/green board from its first run instead of an eyeballed spec.

For each synthetic file we KNOW the answer (mess_maker made the mess). Two checks per file:
    row-set     the clean rows ingest reconstructs, as a SET, must equal the originals
    receipts    each number must be cited at the exact cell mess_maker wrote it to

The record-table case is EXPECTED RED today: current ingest always melts, so it cannot leave a
tidy table alone. That red line is the specification for option 1 — when it turns green, the
classifier works. No model, no network; free and deterministic.
"""
import sys, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "tests"))

import duckdb
import mess_maker as mm
from propose import propose
from ingest_spec import ingest

TMP = pathlib.Path("/tmp/eval_ingest"); TMP.mkdir(exist_ok=True)

# ── the clean tables we start from — small enough to read by eye ──────────────────
FUEL = [{"state": s, "year": y, "tonnes": t} for s, y, t in [
    ("BIHAR", "2019-20", 1161.2), ("BIHAR", "2020-21", 1204.0),
    ("GUJARAT", "2019-20", 5607.6), ("GUJARAT", "2020-21", 5720.3),
    ("DELHI", "2019-20", 998.4), ("DELHI", "2020-21", 1010.1),
    ("PUNJAB", "2019-20", 2450.0), ("PUNJAB", "2020-21", 2501.7),
]]
CLIMB = [{"Name": n, "Symbol": sy, "pivot": p, "dma50": a, "dma200": b} for n, sy, p, a, b in [
    ("Yasho", "NSE:YASHO", 59.0, 50.1, 148.6), ("Morepen", "NSE:MOREPENLAB", 41.1, 44.6, 90.9),
    ("BLS", "NSE:BLSE", 32.6, 15.6, 50.8), ("Univ", "NSE:UNIVCABLES", 29.4, 33.7, 83.8),
    ("Shiv", "NSE:SBCL", 29.3, 30.5, 81.3), ("Kwality", "NSE:KPL", 25.4, 31.0, 109.8),
]]


def actual_rows_and_receipts(db, table):
    con = duckdb.connect(str(db), read_only=True)
    cols = [c[0] for c in con.execute(f"DESCRIBE {table}").fetchall() if not c[0].startswith("__")]
    q = ", ".join(f'"{c}"' for c in cols)
    rows = con.execute(f"SELECT {q} FROM {table}").fetchall()
    # value receipts keyed by row_id
    recs = {}
    for rid, a1 in con.execute(
        "SELECT row_id, a1 FROM cell_map WHERE column_name = ?", [table_measure(db, table)]
    ).fetchall():
        recs[rid] = a1
    idmap = {r[0]: r[1:] for r in con.execute(
        f"SELECT __row_id, {q} FROM {table}").fetchall()}
    con.close()
    return cols, set(rows), recs, idmap


def table_measure(db, table):
    con = duckdb.connect(str(db), read_only=True)
    names = [c[0] for c in con.execute(f"DESCRIBE {table}").fetchall()]
    con.close()
    return "value" if "value" in names else names[-1]


def run(name, xlsx, truth):
    spec = propose(xlsx, planner=None)                 # geometry only — no model needed
    db = TMP / f"{name}.duckdb"
    ingest(spec, xlsx, db)
    _, _, recs, idmap = actual_rows_and_receipts(db, spec.table)

    def norm(t):
        return tuple(round(x, 2) if isinstance(x, float) else str(x) for x in t)

    # Project the ingested table onto (entity, period, measure) — the melted shape. If the file
    # was correctly left as a record table, these columns won't exist and this raises, which is
    # itself the verdict "it did not melt".
    ent, per, meas = spec.entity, spec.period, _meas(spec)
    con = duckdb.connect(str(db), read_only=True)
    try:
        got = {norm(r) for r in con.execute(
            f'SELECT "{ent}", "{per}", "{meas}" FROM {spec.table}').fetchall()}
        melted = True
    except Exception:
        got = set(); melted = False
    total = con.execute(f"SELECT count(*) FROM {spec.table}").fetchone()[0]
    con.close()

    hi = [k for k, v in spec.confidence.items() if v == "high"]
    print(f"\n>>> {name}  (should_melt={truth.should_melt})")

    if truth.should_melt:
        want = {norm(r) for r in truth.rows}
        row_ok = got == want
        rec_ok, rec_detail = receipts_match(spec, idmap, recs, truth)
        print(f"    row-set   {'PASS' if row_ok else 'FAIL'}  got {len(got)}, want {len(want)}")
        if not row_ok and got:
            print(f"        want e.g. {sorted(want)[0]}   got e.g. {sorted(got)[0]}")
        print(f"    receipts  {'PASS' if rec_ok else 'FAIL'}  {rec_detail}")
    else:
        # Record table: correct behaviour is to NOT melt. Current ingest always melts, so it
        # inflates the row count and destroys the record. This red line is option 1's spec.
        kept = (not melted) and total == len(truth.rows)
        rec_ok, rec_detail = record_receipts_ok(spec, db, truth) if kept else (False, "not kept")
        row_ok = kept and rec_ok
        print(f"    no-melt   {'PASS' if kept else 'FAIL'}  "
              f"{'left as records' if not melted else f'MELTED into {total} rows'}, "
              f"want {len(truth.rows)} records untouched")
        print(f"    receipts  {'PASS' if rec_ok else 'FAIL'}  {rec_detail}")
        rec_ok = kept and rec_ok
    print(f"    confidence: {len(hi)} 'high' -> {hi}"
          + ("   <- all 'high' on a WRONG reading" if not row_ok else ""))
    return row_ok and rec_ok


def receipts_match(spec, idmap, recs, truth):
    """Only meaningful when the melt happened as expected. For the record case (no melt today),
    idmap columns won't line up with truth keys — report that honestly rather than faking a pass."""
    ent, per = spec.entity, spec.period
    cols = None
    con = duckdb.connect(str(TMP / f"{spec.table.split('.')[0]}.duckdb"), read_only=True) \
        if False else None
    # find (entity,period)->row_id from idmap using column order
    # idmap values are tuples aligned to DESCRIBE order (minus __row_id). Rebuild header.
    dbcols = _cols_of(spec)
    try:
        ei, pi, vi = dbcols.index(ent), dbcols.index(per), dbcols.index(_meas(spec))
    except ValueError:
        return False, "columns don't match a melted layout (expected for the record case)"
    hits = miss = 0
    for (e, p), cell in [((k[0], k[1]), v) for k, v in truth.cell_of.items() if len(k) == 2]:
        rid = next((r for r, vals in idmap.items()
                    if str(vals[ei]) == str(e) and str(vals[pi]) == str(p)), None)
        if rid is not None and recs.get(rid) == cell:
            hits += 1
        else:
            miss += 1
    return miss == 0 and hits > 0, f"{hits} cells correct, {miss} wrong/missing"


def _cols_of(spec):
    con = duckdb.connect(str(TMP / f"{spec.table}.duckdb"), read_only=True)
    cols = [c[0] for c in con.execute(f"DESCRIBE {spec.table}").fetchall() if not c[0].startswith("__")]
    con.close()
    return cols


def _meas(spec):
    return spec.measure if spec.measure else "value"



def record_receipts_ok(spec, db, truth):
    """Each measure cell must be cited at the address mess_maker wrote it to.

    truth.cell_of maps (id..., measure) -> cell. Match a record's id values to its __row_id, then
    confirm cell_map carries (row_id, measure, cell). Uses the FIRST id column to locate the row.
    """
    con = duckdb.connect(str(db), read_only=True)
    cols = [c[0] for c in con.execute(f"DESCRIBE {spec.table}").fetchall() if not c[0].startswith("__")]
    id0 = truth.id_columns[0]
    idmap = {}
    for row in con.execute(f'SELECT __row_id, "{id0}" FROM {spec.table}').fetchall():
        idmap[str(row[1])] = row[0]
    recs = {(rid, col): a1 for rid, col, a1 in
            con.execute("SELECT row_id, column_name, a1 FROM cell_map").fetchall()}
    con.close()
    hits = miss = 0
    for key, cell in truth.cell_of.items():
        *ids, measure = key
        rid = idmap.get(str(ids[0]))
        if rid is not None and recs.get((rid, measure)) == cell:
            hits += 1
        else:
            miss += 1
    return miss == 0 and hits > 0, f"{hits} measure cells correct, {miss} wrong/missing"


if __name__ == "__main__":
    results = []
    x, t = mm.fold(FUEL, ["state"], "year", "tonnes", TMP / "fold_plain.xlsx")
    results.append(run("fold_plain", x, t))

    x, t = mm.fold(FUEL, ["state"], "year", "tonnes", TMP / "fold_messy.xlsx",
                   blank_rows=2, footnote="Source: made up for the test")
    results.append(run("fold_messy", x, t))

    x, t = mm.keep(CLIMB, ["Name", "Symbol"], ["pivot", "dma50", "dma200"], TMP / "keep_record.xlsx")
    results.append(run("keep_record", x, t))

    print(f"\n{sum(results)}/{len(results)} files fully correct")
    sys.exit(0 if sum(results) == len(results) else 1)
