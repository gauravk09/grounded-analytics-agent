"""The three gates the rest of the pipeline does not already cover.

Most refusal machinery lives where it belongs: Pydantic checks shape, the whitelist checks
names, the compiler checks the slot contract, coverage checks dropped filters, the planner
abstains on missing concepts, and the executor abstains on an empty result. What remains:

  ambiguity  — the question named something matching several labels. ASK (D20), do not guess.
  lineage    — the cited cells must reconstruct the reported number. Failure is OUR bug.
  digits     — no number in the rendered text that came from neither a slot nor the question.
               Structurally impossible (D1), so if it fires the invariant has been broken.

Only the first is about the data. The other two are the system checking itself, and both
report loudly rather than abstaining — claiming "the data cannot answer this" when the real
problem is a bug would be a lie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from answer import Answer                       # noqa: E402
from catalog import Catalog                     # noqa: E402
from plan import QueryPlan                     # noqa: E402

MIN_TERM = 4
FUZZY = 0.82     # 'maharastra' vs 'maharashtra' is 0.95; unrelated states sit far below
MARGIN = 0.08    # two labels within this of each other are a tie, not a winner
FUZZY_MIN = 5    # below this, only literal matching. "least" scores 0.89 against "east"          # shorter words ("the", "most") are never entity names
TOLERANCE = 1e-6      # relative; summing floats in a different order shifts the last digit


@dataclass
class Resolution:
    """How a word in the question maps onto a column's labels.

    Three outcomes, not two — the map / ask / abstain shape used everywhere else in this system
    (D20), finally applied to matching itself:

        none  nothing scores above FUZZY. "Bangalore" is a city, not a state in this file.
        one   a clear winner. "Maharastra" -> MAHARASHTRA at 0.95, next best 0.59.
        many  a tie. "Pradesh" scores 1.00 against five states, so picking one is a guess.

    A pure best-match rule handles the first two and silently picks a winner in the third.
    """
    kind: str                 # "none" | "one" | "many"
    candidates: list[str]
    score: float = 0.0


def _score(word: str, label: str) -> float:
    """Best similarity between a word and a label, or any single word of that label."""
    return max(SequenceMatcher(None, word, part).ratio()
               for part in [label.lower(), *label.lower().split()])


def _score_long(word: str, label: str) -> float:
    """Similarity, but only against label parts long enough for a typo to be plausible."""
    parts = [p for p in [label.lower(), *label.lower().split()] if len(p) >= FUZZY_MIN]
    return max((SequenceMatcher(None, word, p).ratio() for p in parts), default=0.0)


def resolve(word: str, labels: list[str]) -> Resolution:
    """Map one question word onto a column's labels."""
    w = word.lower()
    # Containment is its own kind of ambiguity: "uttar" is a PREFIX of both UTTAR PRADESH and
    # UTTARAKHAND, yet scores 1.00 against the first, so similarity alone calls it a winner.
    inside = [l for l in labels if w in l.lower()]
    if len(inside) > 1:
        return Resolution("many", sorted(inside), 1.0)
    if len(inside) == 1:
        return Resolution("one", inside, 1.0)

    # Fuzzy matching exists for TYPOS, which roughly preserve length. On short words it
    # instead finds unrelated English: "least" scores 0.89 against "east", which is how the
    # first version of this check demanded a region filter for "which region used the least".
    if len(w) < FUZZY_MIN:
        return Resolution("none", [], 0.0)
    scored = sorted(((r, l) for l in labels
                     for r in [_score_long(w, l)] if r > 0), reverse=True)
    if not scored or scored[0][0] < FUZZY:
        return Resolution("none", [], scored[0][0] if scored else 0.0)
    top = scored[0][0]
    tied = [l for r, l in scored if top - r <= MARGIN]
    return Resolution("many" if len(tied) > 1 else "one", tied, top)


@dataclass
class Ambiguity:
    column: str
    term: str
    candidates: list[str]
    chosen: str

    def question(self) -> str:
        opts = " or ".join(self.candidates)
        return f'By "{self.term}" did you mean {opts}?'


class VerificationError(Exception):
    """A gate that should never fire, fired. This is a bug in our code, not a data limit."""


def find_absent_concept(question: str, catalog: Catalog) -> str | None:
    """A concept the catalog declares it does not contain.

    Checked BEFORE planning, because "this file has no tax data" is a fact about the catalog,
    not a judgment for the model. A 3B model gets this wrong; a set lookup cannot.
    """
    q = question.lower()
    for term in catalog.absent:
        if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", q):
            return term
    return None


# Operations a user will reasonably ask for that a single-aggregate plan cannot express.
# Refusing these honestly is different from refusing for missing data: the data IS here, the
# system simply cannot divide two numbers yet. Conflating the two misleads the user about
# whether rephrasing would help.
UNSUPPORTED = {
    "per capita": "a per-capita rate",
    "growth rate": "a growth rate between two periods",
    "cagr": "a compound growth rate",
    # Statistics the typed grammar (sum/avg/min/max/count) cannot express. Listed here so they
    # route to the SQL escape hatch instead of the planner silently mapping "median" to "avg".
    "median": "a median",
    "standard deviation": "a standard deviation",
    "std dev": "a standard deviation",
    "stddev": "a standard deviation",
    "variance": "a variance",
    "percentile": "a percentile",
    "quantile": "a quantile",
    "correlation": "a correlation",
}


def find_unsupported(question: str) -> str | None:
    q = question.lower()
    for term, described in UNSUPPORTED.items():
        if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", q):
            return described
    return None


# "previous year", "last year", "year-over-year" all resolve to a concrete value the question
# never spells out. Flagging that as an invented filter is a false positive — and a costly one,
# because it escalates a correct plan and eventually abstains on an answerable question.
RELATIVE_TIME = re.compile(
    r"\b(previous|prior|last|preceding|earlier|year[- ]on[- ]year|year[- ]over[- ]year|yoy|"
    r"compared to|versus|vs\.?|growth|increase|decrease|change)\b", re.I)


def named_in(question: str, term: str, siblings: list[str],
             aliases: list[str] | None = None) -> bool:
    """Is `term` grounded in this question — literally, by alias, or as its best fuzzy match?

    Module-level, and alias-aware, so the memory layer applies EXACTLY this test when deciding
    whether a filter is `stated`. The first version left aliases out and immediately produced
    two definitions of "stated" that disagreed: "diesel" -> HSD read as stated by one and
    invented by the other.
    """
    q = question.lower()
    t = term.lower()
    # Aliases are exact vocabulary ("diesel" -> HSD), so they only ever match literally.
    for a in aliases or []:
        if re.search(rf"(?<![a-z0-9_]){re.escape(a.lower())}(?![a-z0-9_])", q):
            return True
    if re.search(rf"(?<![a-z0-9_]){re.escape(t)}(?![a-z0-9_])", q):
        return True
    for w in re.findall(r"[a-z0-9][a-z0-9&\-]*", q):
        if len(w) < MIN_TERM:
            continue
        r = resolve(w, siblings)
        if r.kind == "many" and t in [c.lower() for c in r.candidates] \
                and not any(str(l).lower() in q for l in siblings):
            return True
        if r.kind == "one" and r.candidates[0].lower() == t:
            return True
    return False


def overfilter_gaps(question: str, plan, catalog: Catalog, previous=None) -> list[str]:
    """Filters whose value the question never names — the model inventing a constraint.

    The exact mirror of coverage_gaps: coverage catches a DROPPED filter, this catches an
    INVENTED one. Found when "Bihar's share of total consumption of petrol" — a question with
    no year in it — came back scoped to 2024-25, silently answering a narrower question than
    the one asked.

    Compiler defaults are exempt: the compiler adds those on purpose (D9, D24).
    """
    if getattr(plan, "kind", None) == "abstain":
        return []
    q = question.lower()

    words = re.findall(r"[a-z0-9][a-z0-9&\-]*", q)

    def named(term: str, siblings: list[str]) -> bool:
        """Literal match, or the BEST fuzzy match among the column's labels.

        Two failures had to be avoided at once, in opposite directions:
          too strict — "Maharastra" (one missing h) looked like an invented filter, and the
                       system refused an answerable question (D51)
          too loose  — ANDHRA PRADESH and MADHYA PRADESH score 0.86 against each other, so a
                       plain threshold would wave through a genuinely wrong state (D53)

        So closeness alone is not enough: the filter's value must be the label the question
        word matches BEST. If some other label fits better, the model picked the wrong one.
        """
        t = term.lower()
        if re.search(rf"(?<![a-z0-9_]){re.escape(t)}(?![a-z0-9_])", q):
            return True
        for w in words:
            if len(w) < MIN_TERM:
                continue
            r = resolve(w, siblings)
            # A tie is not an invented filter — it is an ambiguity, and find_ambiguity asks
            # about it before we ever get here. Only a clear winner for a DIFFERENT label
            # means the model picked wrong.
            # A tie excuses the filter only when the question did not spell a label out. If
            # it did, ambiguity is settled and picking a different tied label is an error.
            if r.kind == "many" and t in [c.lower() for c in r.candidates] \
                    and not any(str(l).lower() in q for l in siblings):
                return True
            if r.kind == "one" and r.candidates[0].lower() == t:
                return True
        return False

    filters = (list(plan.filters) if plan.kind == "query"
               else [f for m in plan.measures for f in m.filters])
    prev = {}
    if previous is not None:
        pf = (list(previous.filters) if getattr(previous, "kind", None) == "query"
              else [f for m in getattr(previous, "measures", []) for f in m.filters])
        prev = {f.column: f.value for f in pf}
    out = []
    for f in filters:
        if catalog.defaults.get(f.column) == f.value:
            continue
        col = next((c for c in catalog.columns if c.name == f.column), None)
        if col is None or not col.labels:
            continue
        # A comparison question resolves its own periods ("previous year" -> 2024-25), and a
        # percent_change plan differs in the scope dimension by construction.
        if col.scope_dimension and (RELATIVE_TIME.search(question)
                                    or getattr(plan, "kind", None) == "derived"):
            continue
        aliases = [a for a, v in (col.value_aliases or {}).items() if v == f.value]
        # Three legal sources (D56): stated in this turn, an alias for something stated, or
        # inherited from a recent plan. Anything else is still an invention.
        if (named(f.value, col.labels)
                or any(re.search(rf"(?<![a-z0-9_]){re.escape(a.lower())}(?![a-z0-9_])", q)
                       for a in aliases)
                or prev.get(f.column) == f.value):
            continue
        out.append(f"{f.column}={f.value}")
    return sorted(set(out))


# "last year" resolves to a period, but relative to WHAT? "2025-26 vs the previous year" is
# anchored by an explicit period and is fine. "last year" alone is not — and the two readings
# give different answers (7.44% vs 6.29% for Maharashtra), so the choice cannot be silent.
ANCHORLESS = re.compile(r"\b(last|previous|prior|preceding|recent|latest|this)\s+"
                        r"(year|period|fy|quarter)\b", re.I)


def find_unanchored_period(question: str, catalog: Catalog) -> tuple[str, list[str]] | None:
    """A relative period reference with no explicit period to anchor it."""
    if not ANCHORLESS.search(question):
        return None
    q = question.lower()
    for col in catalog.columns:
        if not col.scope_dimension or not col.labels:
            continue
        if any(str(l).lower() in q for l in col.labels):
            return None                    # anchored by an explicit value
        return col.name, col.labels
    return None


def find_scope_gap(question: str, plan, catalog: Catalog) -> tuple[str, list[str]] | None:
    """A scope dimension left unconstrained: the query aggregates across all of it.

    Leaving `product` unset picks a real value (ALL = all products). Leaving `year` unset picks
    nothing — it sums 18 years, answering a broader question than the one asked. A default value
    is a choice WITHIN a dimension; aggregating across an entire dimension is a choice ABOUT
    SCOPE, and only the second needs surfacing.

    Not fired when the dimension is grouped (the answer is broken out by it), or when the
    question names one of its values.
    """
    kind = getattr(plan, "kind", None)
    if kind not in ("query", "derived"):
        return None
    q = question.lower()
    filters = (list(plan.filters) if kind == "query"
               else [f for m in plan.measures for f in m.filters])
    constrained = {f.column for f in filters} | set(getattr(plan, "group_by", []))

    for col in catalog.columns:
        if not col.scope_dimension or col.name in constrained or not col.labels:
            continue
        if any(str(l).lower() in q for l in col.labels):
            continue                      # the user named a value; the planner just missed it
        # An explicit "all years" / "every year" / "across all years" IS a scope choice, so
        # the question has been answered and the check must not fire again.
        if re.search(rf"\b(all|every|across all|combined|total)\s+{re.escape(col.name)}s?\b", q):
            continue
        return col.name, col.labels
    return None


def _tokens(text: str) -> set[str]:
    """Alphanumeric tokens: words of length >= 2, plus bare numbers (50, 200)."""
    return set(re.findall(r"[a-z]{2,}|\d+", str(text).lower()))


def find_measure_gap(question: str, plan, catalog: Catalog) -> list[str] | None:
    """Several measures, none named: the planner picks one silently. Ask which (map/ask/abstain).

    A record workbook has multiple numeric columns (% vs pivot, 50-DMA, 200-DMA). "which stock is
    highest?" names no measure, so any single choice answers a question the user did not ask — the
    same failure as an unconstrained scope dimension, one column over. A crosstab has one measure,
    so this never fires there.

    "Named" is decided on DISTINCTIVE tokens — tokens that belong to exactly one measure — so
    'pivot', '50', '200' identify a measure while the shared 'price', 'vs', 'dma' do not. Matching
    is on whole tokens, not substrings, so '50' does not match '500' (the word-boundary lesson).
    """
    if getattr(plan, "kind", None) != "query":
        return None
    if plan.metric.agg not in ("sum", "avg", "min", "max"):
        return None                       # count needs no measure
    measures = [c for c in catalog.columns
                if c.dtype in ("DOUBLE", "BIGINT") and not c.internal]
    if len(measures) < 2 or plan.metric.column not in {m.name for m in measures}:
        return None

    toksets = {m.name: _tokens(m.name) for m in measures}
    # A token discriminates only if it belongs to EXACTLY ONE measure. "dma" sits in both
    # 50-DMA and 200-DMA, so it must not count as naming either; "200" sits in one, so it does.
    owners: dict[str, int] = {}
    for toks in toksets.values():
        for t in toks:
            owners[t] = owners.get(t, 0) + 1
    distinctive = {name: {t for t in toks if owners[t] == 1} for name, toks in toksets.items()}
    q = _tokens(question)
    named = [m.name for m in measures if distinctive[m.name] & q]
    if len(named) == 1:
        return None                       # exactly one measure named — no ambiguity
    return [m.name for m in measures]     # 0 or several named -> ask which


def check_derivation_shape(plan, catalog: Catalog) -> str | None:
    """The two measures must be shaped correctly FOR THE OPERATION.

    Counting how many filters were dropped is not enough — it passed a plan that divided
    Bihar's 2025-26 petrol by ALL INDIA's 2024-25 petrol and called it growth.

      divide (a share)        the denominator RELAXES the numerator: it may drop columns, but
                              every column they share must hold the SAME value. A denominator
                              on a different year is not a total, it is a different question.
      percent_change/subtract the two measures COMPARE: identical filters except exactly one
                              column, which must be the scope dimension (the period).
    """
    if getattr(plan, "kind", None) != "derived":
        return None
    by = {m.name: {f.column: f.value for f in m.filters} for m in plan.measures}
    num, den = by.get(plan.derive.of, {}), by.get(plan.derive.by, {})
    shared = set(num) & set(den)
    differing = {c for c in shared if num[c] != den[c]}
    scope = {c.name for c in catalog.columns if c.scope_dimension}

    if plan.derive.op == "divide":
        # A share needs a WIDER denominator. If both measures filter the same columns the
        # ratio is 1 by construction — "Maharashtra as a share of Maharashtra" is 100% and
        # never a real question. Without this, a follow-up that edits BOTH measures produces
        # a confident 100% that every other check accepts (D55).
        if not differing and set(den) == set(num):
            return ("a share must divide by something wider than the numerator, but both "
                    "measures filter on exactly the same columns — that ratio is always 100%")
        if differing:
            return (f"a share divides by a total, so the two measures must agree on "
                    f"{', '.join(sorted(differing))} — here they differ "
                    f"({', '.join(f'{c}: {num[c]} vs {den[c]}' for c in sorted(differing))}). "
                    f"For a period-over-period comparison use percent_change")
        return None

    # percent_change / subtract
    if set(num) != set(den):
        return (f"a comparison must hold everything else constant: the two measures filter on "
                f"different columns ({sorted(set(num) ^ set(den))})")
    if len(differing) != 1:
        return (f"a comparison must differ in exactly one dimension, not {sorted(differing)}")
    if scope and not (differing & scope):
        return (f"a comparison should differ in the period, but these differ in "
                f"{sorted(differing)}")
    return None


def find_denominator_ambiguity(plan) -> str | None:
    """A share is only well defined once you know what the total is over.

    Structural rule: the denominator should relax the numerator by exactly ONE dimension —
    "Bihar's share of India's petrol" drops `state` and keeps `product`. Dropping two is a
    second reading the user did not necessarily intend (share of petrol? of all fuels?), and
    the model would pick one silently. So: ask (D20).

    Chosen over phrase-matching on the question because it depends on the plan's shape rather
    than on wording, and therefore works on any dataset.
    """
    if getattr(plan, "kind", None) != "derived" or plan.derive.op != "divide":
        return None
    by = {m.name: {f.column for f in m.filters} for m in plan.measures}
    num, den = by.get(plan.derive.of, set()), by.get(plan.derive.by, set())
    dropped = num - den
    if len(dropped) <= 1:
        return None
    kept = " and ".join(sorted(num - dropped))
    return (f"a share of the total across {', '.join(sorted(dropped))} — did you mean the total "
            f"for the same {kept or 'filters'}, or across those as well?")


def find_ambiguity(question: str, catalog: Catalog) -> list[Ambiguity]:
    """Words in the question matching several labels of the same column.

    Underscore counts as part of a word here. It did not, and the moment the row-kind vocabulary
    became `subtotal` / `grand_total`, the word "total" matched inside BOTH — so "Delhi's total
    consumption" was reported as ambiguous between two row kinds nobody had mentioned. The old
    vocabulary hid it by accident: only `region_total` contained the word.

    Checked BEFORE planning too. Ambiguity is a property of the question against the catalog,
    not of the plan — and by the time a plan exists the model has already resolved it
    invisibly, or abstained, in which case we would never look.
    """
    q = question.lower()
    words = sorted({w for w in re.findall(r"[a-z]{%d,}" % MIN_TERM, q)})
    out = []
    for col in catalog.columns:
        if not col.labels or col.internal:
            # Internal columns are skipped: "Delhi's TOTAL consumption" was reported as ambiguous
            # between `grand_total` and `subtotal` — two words the user did not say and could not
            # have meant, because they are names this pipeline invented.
            continue
        # A label spelled out in full settles the column. Otherwise "Andhra Pradesh" gets
        # flagged as ambiguous because the WORD "pradesh" ties five states — even though the
        # question could not have been clearer.
        if any(str(l).lower() in q for l in col.labels):
            continue
        for w in words:
            if any(w == l.lower() for l in col.labels):
                continue                       # the user named a label exactly
            r = resolve(w, col.labels)
            if r.kind == "many":
                out.append(Ambiguity(col.name, w, r.candidates, ""))
    return out


DERIVATIONS = {
    "divide":         lambda a, b: a / b,
    "subtract":       lambda a, b: a - b,
    "percent_change": lambda a, b: (a - b) / b,
}


def check_lineage(answer: Answer, agg: str) -> None:
    """The cited cells must reconstruct the number. Proves the cited set is exactly the
    contributing set — not a superset, not a subset. Pure arithmetic, no judgment.

    For a derived value the check gets STRONGER, not weaker: each input is verified against
    its own cells, and then the arithmetic between them is verified too.
    """
    for val in answer.slots.values():
        if val.parts:
            _check_derived(val, agg)
    for name, val in answer.slots.items():
        if not isinstance(val.raw, (int, float)) or not val.citations:
            continue
        nums = [float(c.raw_value) for c in val.citations
                if c.raw_value not in (None, "") and _numeric(c.raw_value)]
        if not nums:
            continue
        want = float(val.raw)
        got = {"sum": sum(nums), "avg": sum(nums) / len(nums), "count": float(len(nums))}.get(agg)
        if agg in ("min", "max"):
            ok = any(abs(n - want) <= TOLERANCE * max(1.0, abs(want)) for n in nums)
        elif got is None:
            continue                                  # aggregation we do not know how to verify
        else:
            ok = abs(got - want) <= TOLERANCE * max(1.0, abs(want))
        if not ok:
            raise VerificationError(
                f"slot {name}: {len(nums)} cited cells do not reconstruct {want} "
                f"under {agg} (got {got if got is not None else nums[:3]})"
            )


def _check_derived(val, agg: str) -> None:
    """Verify each input against its cells, then verify the operation between them."""
    for name, part in val.parts.items():
        nums = [float(c.raw_value) for c in part.citations
                if c.raw_value not in (None, "") and _numeric(c.raw_value)]
        if not nums:
            continue
        want = float(part.raw)
        got = {"sum": sum(nums), "avg": sum(nums) / len(nums),
               "count": float(len(nums))}.get(agg)
        if got is not None and abs(got - want) > TOLERANCE * max(1.0, abs(want)):
            raise VerificationError(
                f"derived input {name}: {len(nums)} cited cells give {got} under {agg}, "
                f"but the value is {want}"
            )

    # Then the arithmetic itself. Recovering op and scale from the text keeps Value free of
    # a dependency on the plan module; the derivation string is written by us, not the model.
    if not val.derivation:
        return
    op = ("percent_change" if "(" in val.derivation
          else "divide" if "/" in val.derivation else "subtract")
    scale = float(val.derivation.split(" x ")[-1]) if " x " in val.derivation else 1.0
    names = list(val.parts)
    a, b = float(val.parts[names[0]].raw), float(val.parts[names[1]].raw)
    if b == 0:
        return
    expected = DERIVATIONS[op](a, b) * scale
    if abs(expected - float(val.raw)) > TOLERANCE * max(1.0, abs(expected)):
        raise VerificationError(
            f"derivation {val.derivation!r} on {a} and {b} gives {expected}, "
            f"but the reported value is {val.raw}"
        )


def check_digits(answer: Answer, question: str, filter_values: list[str] | None = None) -> None:
    """No number in the rendered text that came from neither a slot nor the question.

    Should never fire — narration is a template and numbers are substituted from Values. If it
    does, someone has reintroduced a path for the model to write digits."""
    allowed = set(_nums(question))
    for fv in filter_values or []:
        allowed.update(_nums(str(fv)))

    def collect(v) -> None:
        allowed.update(_nums(v.formatted))
        # A unit can contain digits. PPAC's own header cell reads "(`000 Metric Tonnes)", and the
        # spec now carries that string verbatim rather than a tidied "thousand metric tonnes" — so
        # "27,237.19 000 Metric Tonnes" tripped this gate on the literal 000. A unit is a LABEL
        # copied from the file, not a computed figure, so its digits are backed by definition.
        # Whitelisting the unit is safe in a way that relaxing the pattern would not be: the unit
        # is a fixed string we already hold, not an arbitrary shape we would have to guess.
        if v.unit:
            allowed.update(_nums(v.unit))
        for part in v.parts.values():
            collect(part)

    for v in answer.slots.values():
        collect(v)
    stray = set(_nums(answer.text())) - allowed
    if stray:
        raise VerificationError(
            f"rendered text contains unbacked number(s) {sorted(stray)}: {answer.text()!r}"
        )


def _nums(text: str) -> list[str]:
    # Must END on a digit, so a trailing comma or full stop is not swallowed: "2024-25," would
    # otherwise never match the "2024-25" in the question. Internal , - / . are kept, because
    # formatted numbers look like 27,551.84 and years look like 2024-25.
    return re.findall(r"\d(?:[\d,\-/.]*\d)?", text)


def _numeric(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False
