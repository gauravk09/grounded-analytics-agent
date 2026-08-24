"""Build the compact description of the data that the planner sees.

Two rules this module exists to enforce:
  D17 — structure goes in the catalog, values go through the engine. Column names, types,
        null counts, distinct counts and low-cardinality labels are structure. Means, ranges
        and sample rows are values, and never appear here.
  D18 — aliases are an explicit allowlist. Anything not on it abstains rather than being
        matched to its nearest neighbour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

MAX_LABELS = 50   # above this, a column's values are data, not structure

# Concepts a user will plausibly ask about that this file does NOT contain. Declaring them
# turns "can this be answered?" into a lookup instead of a model judgment — the mirror of
# D17. Generated at upload alongside the aliases (D21); hand-written for now.
ABSENT_CONCEPTS = [
    "tax", "taxes", "revenue", "price", "prices", "cost", "margin", "profit",
    "population", "gdp", "vehicles", "imports", "exports", "production", "refinery",
]

# Hand-authored semantic layer. Derived from the workbook where possible:
# 'thousand metric tonnes' is cell A6, the MS/HSD expansions are the sheet names.
# Moves into specs/ppac.yaml when D16 lands.
ANNOTATIONS: dict[str, dict] = {
    "state": {
        "description": "Indian state or union territory",
        "aliases": ["state", "states", "ut", "union territory", "region name"],
    },
    "region": {
        "description": "zone the state belongs to, from the sheet's section headers",
        "aliases": ["region", "zone", "area"],
    },
    "year": {
        "description": "Indian financial year, April to March, written like 2019-20",
        "aliases": ["year", "financial year", "fy", "fiscal year", "period"],
    },
    "product": {
        "description": (
            "fuel type. ALL = all petroleum products; "
            "MS = Motor Spirit (petrol, gasoline); "
            "HSD = High Speed Diesel (diesel)"
        ),
        "aliases": ["product", "fuel", "fuel type"],
        "value_aliases": {"petrol": "MS", "gasoline": "MS", "motor spirit": "MS",
                          "diesel": "HSD", "hsd": "HSD", "high speed diesel": "HSD",
                          "all products": "ALL", "all fuels": "ALL", "total consumption": "ALL"},
    },
    "value": {
        "description": (
            "quantity consumed, in thousand metric tonnes. "
            "This is a VOLUME. The file contains no prices, revenue or tax."
        ),
        "aliases": ["consumption", "volume", "quantity", "usage", "demand", "tonnes", "sales volume"],
    },
    "row_kind": {
        "description": (
            "whether the row is a real state or a published total. "
            "state = one state; region_total = zonal subtotal; all_india = national total. "
            "Aggregations default to state only, to avoid double counting."
        ),
        "aliases": ["row kind", "row type", "total", "aggregate"],
    },
}


# A "scope dimension" is one where leaving it unconstrained does not select a value — it
# silently AGGREGATES ACROSS the whole dimension. Summing 18 years when the question named no
# year answers a different question, so it is worth asking about (D48).
#
# Inferred rather than declared, because a wrong guess here only changes whether we ask a
# question — never a number. That is a far smaller blast radius than guessing a header row
# (D16), which is why inference is defensible here and not there.
PERIOD_PATTERNS = [
    r"^\d{4}-\d{2}$",            # 2008-09
    r"^\d{4}-\d{4}$",            # 2008-2009
    r"^\d{4}$",                   # 2024
    r"^Q[1-4][ -]?\d{2,4}$",      # Q1-2024
    r"^[A-Z][a-z]{2}-\d{2,4}$",   # Jan-24
    r"^\d{4}-\d{2}-\d{2}$",      # ISO date
]


def looks_periodic(labels: list[str] | None) -> bool:
    if not labels or len(labels) < 3:
        return False
    for pat in PERIOD_PATTERNS:
        if sum(bool(re.match(pat, str(l).strip())) for l in labels) >= 0.8 * len(labels):
            return True
    return False


@dataclass
class Column:
    name: str
    dtype: str
    nulls: int
    distinct: int
    labels: list[str] | None      # None when cardinality is too high to be structure
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    value_aliases: dict[str, str] = field(default_factory=dict)
    scope_dimension: bool = False
    # Our vocabulary, not the file's. `row_kind` is invented by ingest; a user never types
    # "grand_total". Internal columns are still described to the planner and still filterable —
    # they are only excluded from checks that ask "did the USER name this?", because the answer
    # is always no and a false yes is expensive.
    internal: bool = False


# Every workbook has entity rows and aggregate rows; only the entity kind is ever the default.
# This is structural, not file-specific, which is why it is the one default that lives in code.
ENTITY_KIND = "entity"

# `row_kind` is produced by ingest for every workbook, so what it MEANS is structural and belongs
# in code — not in a per-file spec, where it can only rot. It rotted once already: the spec still
# described the old vocabulary ("state = one state; region_total = ..."), so the planner faithfully
# emitted `row_kind="state"` against a table whose values had become entity/subtotal/grand_total,
# and two end-to-end cases matched zero rows.
ROW_KIND_ANNOTATION = {
    "description": ("whether the row is a single item or a published total. "
                    "entity = one item; subtotal = a group total; grand_total = the overall "
                    "total. Aggregations default to entity rows only, to avoid double counting."),
    "aliases": ["row kind", "row type", "aggregate"],
}


@dataclass
class Catalog:
    table: str
    columns: list[Column]
    notes: list[tuple[str, str, str]]     # (sheet, a1, text) — citable context
    absent: list[str] = field(default_factory=list)
    # Annotation keys that name no column. Kept rather than dropped silently — an annotation that
    # matches nothing is invisible: aliases simply fail to exist, and the system refuses questions
    # it can answer. The proposer once keyed these by the sheet's HEADER LABEL ("STATE/UT") instead
    # of the column name ("state"), which cost every fuel alias on the file.
    unknown_annotations: list[str] = field(default_factory=list)

    # What this workbook's rows, columns and numbers ARE. Named by a human at confirmation time;
    # nothing in the structure reveals them, and nothing else may supply them.
    entity: str = "entity"
    period: str = "period"
    measure: str = "value"
    unit: str | None = None

    # Values the compiler injects when a question does not mention them. Sourced from the
    # workbook's own spec — a default is a claim about THIS file, and inheriting one file's
    # defaults into another is how the Union Budget came to declare it had no revenue.
    file_defaults: dict[str, str] = field(default_factory=dict)

    @property
    def defaults(self) -> dict[str, str]:
        return {"row_kind": ENTITY_KIND, **self.file_defaults}

    @property
    def column_names(self) -> set[str]:
        """The whitelist. Anything the planner references must be in here."""
        return {c.name for c in self.columns}

    def labels_for(self, column: str) -> list[str]:
        for c in self.columns:
            if c.name == column:
                return c.labels or []
        return []

    def to_prompt(self) -> str:
        lines = [f"Table: {self.table}"]
        for c in self.columns:
            lines.append(f"  {c.name} ({c.dtype}) — {c.description}")
            if c.labels:
                lines.append(f"      values: {', '.join(c.labels)}")
            if c.aliases:
                lines.append(f"      also called: {', '.join(c.aliases)}")
        return "\n".join(lines)


def build(db_path: Path, table: str = "consumption",
          annotations: dict | None = None, absent: list[str] | None = None,
          spec=None) -> Catalog:
    """Describe a warehouse table so the planner can see it — structure only, never values (D17).

    `annotations` and `absent` default to this module's PPAC constants so existing callers are
    unchanged, but a confirmed `Spec` carries its own. Once more than one workbook is served, the
    catalog must come from that workbook's spec: a file's absent-concepts list causes hard refusals
    before any model runs, so sharing one list across files would refuse answerable questions.
    """
    ann_all = (spec.annotations if spec is not None
               else ANNOTATIONS if annotations is None else annotations)
    is_record = spec is not None and any(getattr(sh, 'layout', 'crosstab') == 'record'
                                         for sh in spec.sheets)
    con = duckdb.connect(str(db_path), read_only=True)
    columns = []

    for name, dtype, *_ in con.execute(f"DESCRIBE {table}").fetchall():
        if name.startswith("__"):
            continue        # plumbing, not something anyone asks about

        nulls, distinct = con.execute(
            f'SELECT count(*) FILTER (WHERE "{name}" IS NULL), count(DISTINCT "{name}") FROM {table}'
        ).fetchone()

        labels = None
        if distinct <= MAX_LABELS:
            labels = [
                str(r[0]) for r in con.execute(
                    f'SELECT DISTINCT "{name}" FROM {table} WHERE "{name}" IS NOT NULL ORDER BY 1'
                ).fetchall()
            ]

        ann = ROW_KIND_ANNOTATION if name == "row_kind" else ann_all.get(name, {})
        # Explicit declaration wins; otherwise infer from the shape of the labels — but ONLY for a
        # crosstab. In a record table a period-looking column (e.g. "Breakout date") is an ATTRIBUTE
        # of one row, not an axis you aggregate across, so pattern-scoping it wrongly asks
        # "which date did you mean?" for a question that named a company. A record's measures are
        # the numbers; its identifiers are for grouping and filtering, never scope.
        scope = ann.get("scope_dimension", False if is_record else looks_periodic(labels))
        columns.append(Column(name, dtype, nulls, distinct, labels,
                              ann.get("description", ""), ann.get("aliases", []),
                              ann.get("value_aliases", {}), scope, name == "row_kind"))

    notes = con.execute("SELECT sheet, a1, text FROM sheet_notes").fetchall()
    con.close()
    unknown = sorted(set(ann_all) - {c.name for c in columns})
    if spec is not None:
        return Catalog(table, columns, notes, list(spec.absent_concepts), unknown,
                       spec.entity, spec.period, spec.measure, spec.unit, dict(spec.defaults))
    return Catalog(table, columns, notes,
                   list(ABSENT_CONCEPTS) if absent is None else list(absent), unknown,
                   file_defaults={"product": "ALL"})


if __name__ == "__main__":
    from workbook import load
    _spec, _db, cat = load()
    print(cat.to_prompt())
