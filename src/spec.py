"""The frozen interpretation of one workbook.

Everything `ingest.py` used to hardcode lives here instead. Once a spec is confirmed, ingest is
100% deterministic — the proposer never runs again for that file.

Two halves, deliberately separated because they have different failure modes:

    geometry  where the header is, which columns hold values, which rows are totals.
              Mechanically derivable from structure, and checkable by looking at the sheet.

    meaning   what the label column IS ("state", "account head"), what the unpivoted header
              dimension should be called ("year", "quarter"), the aliases and descriptions.
              Not derivable from structure at all — it is language, and needs a model or a human.

Getting geometry wrong makes every number and every citation wrong. Getting meaning wrong makes
the assistant worse at understanding questions. Only the first is dangerous, which is why the
geometry fields carry confidence and the meaning fields do not.
"""

from __future__ import annotations

from pathlib import Path

import re

import yaml
from pydantic import BaseModel, Field, field_validator


class SheetSpec(BaseModel):
    name: str

    # Which shape this sheet is. "crosstab" = the header row holds VALUES of one variable
    # (years), so ingest unpivots — the original PPAC assumption. "record" = the header row holds
    # NAMES of different variables (a tidy table already), so ingest leaves the columns alone.
    # Deciding this wrong is the climbing-file bug, so it carries confidence and is confirmed.
    layout: str = "crosstab"
    # Record layout only: which columns identify a row, which hold the numbers. Letters, because
    # a record sheet has no single label_column / value-block the crosstab fields describe.
    id_columns: list[str] = Field(default_factory=list)
    measure_columns: list[str] = Field(default_factory=list)

    # ── geometry ────────────────────────────────────────────────────────────────
    header_row: int = Field(description="row whose cells name the value columns")
    # A stacked header spans several rows (year on one, estimate-type on the next). When set,
    # ingest combines the cells top-to-bottom into one period label. Empty = single header row.
    header_rows: list[int] = Field(default_factory=list)
    label_column: str = Field(description="column letter holding the row's entity name")
    alt_label_columns: list[str] = Field(default_factory=list,
                                         description="same name in another language, or "
                                                     "numbering/indentation to its left")
    first_data_row: int
    last_data_row: int
    first_value_column: str
    last_value_column: str
    note_rows: list[int] = Field(default_factory=list,
                                 description="titles, units, sources — citable, not data")

    # ── interpretation ──────────────────────────────────────────────────────────
    # A label matching one of these patterns marks the row as that kind rather than a plain
    # entity. Aggregate rows are KEPT and tagged, never dropped (D9).
    row_kinds: dict[str, str] = Field(default_factory=dict)
    # A section header defines a dimension for the rows beneath it, and its CELL is what those
    # rows cite for that dimension (D10).
    section_dimension: str | None = None
    # Constants this sheet contributes — how one sheet differs from its siblings.
    constants: dict[str, str] = Field(default_factory=dict)


class Spec(BaseModel):
    file: str
    table: str = "data"
    entity: str = Field(default="entity", description="what the label column holds")
    period: str = Field(default="period", description="what the header row holds")
    measure: str = Field(default="value", description="what the numbers are")
    unit: str | None = None
    sheets: list[SheetSpec]

    annotations: dict[str, dict] = Field(default_factory=dict)
    absent_concepts: list[str] = Field(default_factory=list)
    # Values the compiler injects when a question does not mention them. Per workbook, because
    # they are claims about THIS file: that the fuel file's sheets overlap so `product` must
    # default to ALL is true of that file and of nothing else. `row_kind` is not listed — every
    # workbook defaults to entity rows, which is structural rather than file-specific.
    defaults: dict[str, str] = Field(default_factory=dict)

    # Per-field confidence from the proposer, so a reviewer knows what to look at first.
    # Anything below `high` is what the confirmation screen should highlight.
    confidence: dict[str, str] = Field(default_factory=dict)
    notes_for_reviewer: list[str] = Field(default_factory=list)

    @field_validator("table")
    @classmethod
    def _table_is_plain_identifier(cls, v: str) -> str:
        # `table` is the one field that reaches SQL UNQUOTED — DROP/CREATE/INSERT in ingest,
        # DESCRIBE/SELECT in catalog — so a name like `x; DROP TABLE cell_map; --` would run as
        # commands (D90). It is machine-generated from the filename, so a plain identifier costs
        # nothing. Enforced here, at the one seam both the API (`model_validate`) and the CLI
        # (`Spec(**yaml)`) pass through: an injectable spec cannot be constructed anywhere.
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError(
                f"table name {v!r} is not a plain SQL identifier (letters, digits, underscore; "
                f"not starting with a digit). It flows unquoted into SQL, so this is refused."
            )
        return v

    @field_validator("file")
    @classmethod
    def _file_is_bare_name(cls, v: str) -> str:
        # Every workbook lives flat in data/, and the API joins `DATA / spec.file`. A value like
        # `../../etc/passwd.xlsx` would read outside data/ (review finding #6). Forcing a bare
        # filename here — at the seam both the API and CLI build a Spec through — makes traversal
        # unconstructable, so no individual call site has to remember to basename.
        if v != Path(v).name or v in ("", ".", ".."):
            raise ValueError(f"file {v!r} must be a bare filename, not a path")
        return v

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(self.model_dump(exclude_none=True), sort_keys=False,
                                       allow_unicode=True))

    @classmethod
    def load(cls, path: Path) -> "Spec":
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))
