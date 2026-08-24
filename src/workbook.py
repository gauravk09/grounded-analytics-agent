"""Open a workbook the way the product opens one: through its confirmed spec.

Before this, `__main__` demos and the test suites read `data/warehouse.duckdb` — the database
produced by the hand-written `ingest.py`, whose constants were typed in by a person who had looked
at the fuel file. Everything therefore tested a database the product does not ship, and the
difference was invisible until the row-kind vocabulary changed underneath it and seven end-to-end
cases silently matched zero rows.

A workbook is a spec plus the database that spec produced. There is no other way in.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from catalog import Catalog, build          # noqa: E402
from spec import Spec                       # noqa: E402

# The demo workbook, used by CLI entry points and the suites. Named here once so that "what the
# demo opens" is a single visible fact rather than nine copies of a filename.
DEFAULT = ROOT / "specs" / "ppac.yaml"


def db_for(spec: Spec) -> Path:
    return ROOT / "data" / f"{Path(spec.file).stem}.duckdb"


def load(spec_path: Path | str = DEFAULT) -> tuple[Spec, Path, Catalog]:
    spec = Spec.load(Path(spec_path))
    db = db_for(spec)
    return spec, db, build(db, spec.table, spec=spec)
