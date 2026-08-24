"""The Plan: the only thing the planner is allowed to return.

Two shapes, discriminated on `kind` (D5) — a query, or a refusal. Refusal sits beside
answering as an equally valid output, not in an error path.

This module is deliberately free of any model or database code. It is the seam described in
D22: whatever produces a Plan — a 3B model filling a form today, a frontier model writing SQL
later — everything downstream sees this and only this.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

class PlanFailure(Exception):
    """The model could not produce a usable plan. Distinct from Abstain, which means the
    *data* cannot answer the question. Only PlanFailure is worth escalating to a stronger
    model — escalating a legitimate refusal would just cost money to refuse again.

    Lives here, with no dependencies, so both planner.py and compile.py can raise it.
    """


# Compiler defaults used to live here as a literal — {"product": "ALL", "row_kind": "state"} —
# which made every gate that consults them specific to one workbook. They are now
# `Catalog.defaults`, assembled from the structural entity kind plus whatever THIS file's spec
# declares. Nothing in this module knows what a "product" is.

AGGS = Literal["sum", "avg", "min", "max", "count"]
OPS = Literal["=", "!=", ">", "<", ">=", "<="]


class Filter(BaseModel):
    column: str
    op: OPS
    value: str


class Metric(BaseModel):
    agg: AGGS
    column: str


class QueryPlan(BaseModel):
    kind: Literal["query"]
    metric: Metric
    filters: list[Filter] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    sort: Literal["asc", "desc", "none"] = "none"
    limit: int = Field(default=0, description="0 means no limit. Use 1 for 'which X is highest'.")
    narration: str = Field(
        description="Sentence with numbered holes, e.g. 'X used {v1} tonnes.' "
                    "Must contain no digits of its own."
    )


REASONS = Literal[
    "no_such_column",        # the concept is absent: tax, revenue, population
    "value_not_in_column",   # the column exists, that value does not: year 2030-31
    "not_a_data_question",   # no metric can be extracted at all
    "empty_result",          # a valid query that matched nothing
    "unsupported_operation", # the data supports it; this system cannot express it (ratios, growth)
    "planner_failed",        # a fine question; no planner could build a reliable plan for it
]


class Measure(BaseModel):
    """One aggregate with its own filters. A DerivedPlan holds two of them."""
    name: str = Field(description="m1, m2 — referenced by the derivation")
    agg: AGGS
    column: str
    filters: list[Filter] = Field(default_factory=list)


class Derivation(BaseModel):
    """How to combine two measures. The arithmetic is done by OUR code, not the model (D1)."""
    op: Literal["divide", "subtract", "percent_change"]
    of: str
    by: str
    scale: float = Field(default=1.0, description="100 turns a fraction into a percentage")


class DerivedPlan(BaseModel):
    """A number that exists nowhere in the spreadsheet, computed from two that do.

    Covers share, growth rate, difference and per-unit with one shape — they are all
    "two measures and an operation". Its lineage is a tree: the derived value cites nothing
    directly, and each of its inputs cites its own cells.
    """
    kind: Literal["derived"]
    measures: list[Measure] = Field(min_length=2, max_length=2)
    derive: Derivation
    narration: str


class Abstain(BaseModel):
    """A refusal the model CHOOSES, but does not narrate.

    The model picks a code and names the column or value at issue. The sentence the user reads
    is composed by us, from the catalog — same move as D1: the model decides, the engine writes
    anything factual. Free-form reasons were producing false statements about the data
    ("the table has data only up to 2023-24" when it runs to 2025-26).
    """
    kind: Literal["abstain"]
    reason_code: REASONS
    detail: str = Field(default="", description="the column or value at issue, if any")


Plan = Annotated[Union[QueryPlan, DerivedPlan, Abstain], Field(discriminator="kind")]


class PlanEnvelope(BaseModel):
    """Wrapper so the union has a single schema to hand to constrained decoding."""
    plan: Plan
