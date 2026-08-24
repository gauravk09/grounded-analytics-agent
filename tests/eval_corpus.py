"""Ingestion robustness across DIFFERENT documents — the "does it work on any file" scoreboard.

Each case starts from a clean table we can read by eye, is deformed into a known shape (via
mess_maker or a CSV writer), and must round-trip: the rows ingest reconstructs (as a SET) equal the
originals, and each value is cited at the exact cell it was written to. Because we made the mess, we
know the answer without eyeballing ingest's output.

Covers the axes a real corpus varies on: record vs cross-tab, one vs many measures, few vs many
periods, quarterly/date/code labels, blank rows + footnotes, and CSV as well as XLSX.
"""
import csv as _csv
import sys, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "tests"))

import duckdb
import mess_maker as mm
from openpyxl.utils import get_column_letter
from propose import propose
from ingest_spec import ingest

TMP = pathlib.Path("/tmp/eval_corpus"); TMP.mkdir(exist_ok=True)


def write_csv_fold(clean, id_col, var_col, val_col, out):
    """A cross-tab written as CSV, with the same answer key mess_maker.fold produces."""
    variables = list(dict.fromkeys(r[var_col] for r in clean))
    entities = list(dict.fromkeys(r[id_col] for r in clean))
    lookup = {(r[id_col], r[var_col]): r[val_col] for r in clean}
    rows_out = [[id_col] + variables]
    cell_of = {}
    for i, ent in enumerate(entities, start=2):
        row = [ent]
        for j, v in enumerate(variables, start=2):
            row.append(lookup.get((ent, v), ""))
            if (ent, v) in lookup:
                cell_of[(ent, v)] = f"{get_column_letter(j)}{i}"
        rows_out.append(row)
    with open(out, "w", newline="") as f:
        _csv.writer(f).writerows(rows_out)
    return out, mm.Truth(rows={(r[id_col], r[var_col], r[val_col]) for r in clean},
                         cell_of=cell_of, should_melt=True, id_columns=[id_col])


def check(name, xlsx, truth):
    spec = propose(xlsx, planner=None)
    db = TMP / f"{name}.duckdb"
    ingest(spec, xlsx, db)
    con = duckdb.connect(str(db), read_only=True)
    layout = spec.sheets[0].layout

    def norm(t): return tuple(round(x, 2) if isinstance(x, float) else str(x) for x in t)

    if truth.should_melt:
        ent, per, meas = spec.entity, spec.period, (spec.measure or "value")
        try:
            got = {norm(r) for r in con.execute(f'SELECT "{ent}","{per}","{meas}" FROM {spec.table}').fetchall()}
        except Exception:
            got = set()
        want = {norm(r) for r in truth.rows}
        row_ok = got == want
        # receipts: each (entity,period) cell
        recs = {(rid, c): a1 for rid, c, a1 in con.execute("SELECT row_id,column_name,a1 FROM cell_map").fetchall()}
        idmap = {}
        for row in con.execute(f'SELECT __row_id,"{ent}","{per}" FROM {spec.table}').fetchall():
            idmap[(str(row[1]), str(row[2]))] = row[0]
        miss = sum(1 for (e, p), cell in truth.cell_of.items()
                   if recs.get((idmap.get((str(e), str(p))), meas)) != cell)
        rec_ok = miss == 0
        expected = "crosstab"
    else:
        total = con.execute(f"SELECT count(*) FROM {spec.table}").fetchone()[0]
        row_ok = layout == "record" and total == len(truth.rows)
        id0 = truth.id_columns[0]
        recs = {(rid, c): a1 for rid, c, a1 in con.execute("SELECT row_id,column_name,a1 FROM cell_map").fetchall()}
        idmap = {str(v): rid for rid, v in con.execute(f'SELECT __row_id,"{id0}" FROM {spec.table}').fetchall()}
        miss = sum(1 for k, cell in truth.cell_of.items()
                   if recs.get((idmap.get(str(k[0])), k[-1])) != cell)
        rec_ok = miss == 0
        expected = "record"
    con.close()
    ok = row_ok and rec_ok and layout == expected
    print(f"  {'PASS' if ok else 'FAIL'}  {name:22} layout={layout:8} "
          f"{'rows✓' if row_ok else 'rows✗'} {'cites✓' if rec_ok else 'cites✗'}")
    return ok


# ── the corpus ────────────────────────────────────────────────────────────────
def rows(n, cols): return [dict(zip(cols, v)) for v in n]

CASES = []

# 1. record, 3 measures (climbing-like)
CLIMB = rows([("Yasho", "NSE:YASHO", 59.0, 50.1, 148.6), ("Morepen", "NSE:MOREPEN", 41.1, 44.6, 90.9),
              ("BLS", "NSE:BLSE", 32.6, 15.6, 50.8), ("Univ", "NSE:UNIV", 29.4, 33.7, 83.8),
              ("Shiv", "NSE:SBCL", 29.3, 30.5, 81.3)], ["Name", "Symbol", "pivot", "dma50", "dma200"])
CASES.append(("record_3measure", *mm.keep(CLIMB, ["Name", "Symbol"], ["pivot", "dma50", "dma200"], TMP / "a.xlsx")))

# 2. record, 5 measures + a date + a category
SALES = rows([("Alpha", "SaaS", "2026-01-04", 120.0, 30.0, 12.0, 4.0, 8.0),
              ("Beta", "Retail", "2026-02-11", 88.0, 20.0, 9.0, 3.0, 6.0),
              ("Gamma", "SaaS", "2026-03-09", 200.0, 55.0, 30.0, 9.0, 21.0),
              ("Delta", "Retail", "2026-01-30", 45.0, 10.0, 4.0, 1.0, 3.0)],
             ["Company", "Segment", "Signed", "ARR", "Seats", "Revenue", "Cost", "Margin"])
CASES.append(("record_wide", *mm.keep(SALES, ["Company", "Segment", "Signed"],
                                       ["ARR", "Seats", "Revenue", "Cost", "Margin"], TMP / "b.xlsx")))

# 3. record, single measure
POP = rows([("Delhi", 33.8), ("Mumbai", 21.3), ("Kolkata", 15.1), ("Chennai", 11.5), ("Pune", 7.4)],
           ["City", "Millions"])
CASES.append(("record_1measure", *mm.keep(POP, ["City"], ["Millions"], TMP / "c.xlsx")))

# 4. crosstab, few periods
FUEL = [{"state": s, "year": y, "t": t} for s, y, t in [
    ("BIHAR", "2019-20", 1161.2), ("BIHAR", "2020-21", 1204.0), ("GUJARAT", "2019-20", 5607.6),
    ("GUJARAT", "2020-21", 5720.3), ("DELHI", "2019-20", 998.4), ("DELHI", "2020-21", 1010.1),
    ("PUNJAB", "2019-20", 2450.0), ("PUNJAB", "2020-21", 2501.7)]]
CASES.append(("crosstab_years", *mm.fold(FUEL, ["state"], "year", "t", TMP / "d.xlsx")))

# 5. crosstab, many periods (10), blank rows + footnote
WIDE = [{"co": c, "q": q, "v": round(10 + i * 1.5 + j, 1)}
        for i, c in enumerate(["Acme", "Globex", "Initech", "Umbrella"])
        for j, q in enumerate([f"Q{(k % 4) + 1}-{2024 + k // 4}" for k in range(10)])]
CASES.append(("crosstab_quarters", *mm.fold(WIDE, ["co"], "q", "v", TMP / "e.xlsx",
                                            blank_rows=2, footnote="Source: internal")))

# 6. crosstab, single entity, many periods
ONE = [{"metric": "revenue", "year": str(y), "v": float(100 + (y - 2015) * 12)} for y in range(2015, 2026)]
CASES.append(("crosstab_1entity", *mm.fold(ONE, ["metric"], "year", "v", TMP / "f.xlsx")))

# 7. record as CSV
def _csv_record():
    out = TMP / "g.csv"
    with open(out, "w", newline="") as f:
        w = _csv.writer(f); w.writerow(["City", "Millions"])
        for r in POP: w.writerow([r["City"], r["Millions"]])
    cell_of = {(r["City"], "Millions"): f"B{i}" for i, r in enumerate(POP, start=2)}
    return out, mm.Truth(rows={(r["City"], r["Millions"]) for r in POP},
                         cell_of=cell_of, should_melt=False, id_columns=["City"])

CASES.append(("csv_record", *_csv_record()))

# 8. crosstab as CSV
CASES.append(("csv_crosstab", *write_csv_fold(FUEL, "state", "year", "t", TMP / "h.csv")))


if __name__ == "__main__":
    passed = 0
    for name, xlsx, truth in CASES:
        try:
            passed += check(name, xlsx, truth)
        except Exception as e:
            print(f"  FAIL  {name:22} raised {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(CASES)} documents ingested correctly")
    sys.exit(0 if passed == len(CASES) else 1)
