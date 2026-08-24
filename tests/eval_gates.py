"""Two-sided tests for every gate. Fast, free, and the suite that should be large.

The gates are pure functions — no model, no network — so they can be tested exhaustively in
milliseconds. eval_e2e.py proves the system works end to end and stays small because it costs
money; this proves the gates are correctly calibrated and can be as big as we like.

EVERY gate gets both lists:
    fires  — inputs it must catch
    quiet  — inputs it must NOT catch

Five real bugs came from testing only the first list. Each one refused an answerable question.
"""
import sys, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))

from catalog import build
from spec import Spec
from compile import compile_plan, compile_measure, check_derived
from plan import (Abstain, Derivation, DerivedPlan, Filter, Measure, Metric, PlanFailure,
                  QueryPlan)
from planner import coverage_gaps          # lives with the cascade that consumes it
from verify import (check_digits, check_derivation_shape, find_absent_concept, find_ambiguity,
                    find_scope_gap, find_unanchored_period, find_unsupported, overfilter_gaps)

# Built from the SPEC, like the app does — not from the hand-written warehouse. A suite that
# tests a differently-built database tests something the product does not ship.
SPEC = Spec.load(root / "specs" / "ppac.yaml")
CAT = build(root / "data" / "ppac_statewise_sales.duckdb", SPEC.table, spec=SPEC)


def q(filters=(), group_by=(), agg="sum", col="value", narration="{v1}", sort="none", limit=0):
    return QueryPlan(kind="query", metric=Metric(agg=agg, column=col),
                     filters=[Filter(column=c, op="=", value=v) for c, v in filters],
                     group_by=list(group_by), sort=sort, limit=limit, narration=narration)


def d(num, den, op="divide", scale=100.0):
    return DerivedPlan(kind="derived", derive=Derivation(op=op, of="m1", by="m2", scale=scale),
                       measures=[Measure(name=n, agg="sum", column="value",
                                         filters=[Filter(column=c, op="=", value=v) for c, v in f])
                                 for n, f in (("m1", num), ("m2", den))],
                       narration="{v1}")


BIHAR_MS_25 = [("state", "BIHAR"), ("product", "MS"), ("year", "2024-25")]
ALL_MS_25 = [("product", "MS"), ("year", "2024-25")]

CASES: list[tuple[str, str, bool, object]] = [
    # (gate, label, should_fire, callable -> truthy when it fires)

    # ---- absent concept: the file has volumes only -------------------------------------
    ("absent", "tax revenue", True, lambda: find_absent_concept("how much tax revenue?", CAT)),
    ("absent", "population", True, lambda: find_absent_concept("population of Kerala?", CAT)),
    ("absent", "price", True, lambda: find_absent_concept("petrol price in Delhi?", CAT)),
    ("absent", "plain consumption", False, lambda: find_absent_concept("Bihar petrol consumption 2024-25?", CAT)),
    ("absent", "'taxi' is not 'tax'", False, lambda: find_absent_concept("taxi fuel use in Delhi?", CAT)),
    ("absent", "'production' in prose", True, lambda: find_absent_concept("refinery production?", CAT)),

    # ---- unsupported operation ---------------------------------------------------------
    ("unsupported", "per capita", True, lambda: find_unsupported("petrol per capita in Bihar?")),
    ("unsupported", "cagr", True, lambda: find_unsupported("cagr of diesel demand?")),
    ("unsupported", "share is supported now", False, lambda: find_unsupported("Bihar's share of petrol?")),
    ("unsupported", "percentage is supported", False, lambda: find_unsupported("what percentage was Bihar?")),
    ("unsupported", "growth is supported", False, lambda: find_unsupported("growth in Bihar petrol 2024-25?")),

    # ---- unanchored period -------------------------------------------------------------
    ("anchor", "bare 'last year'", True, lambda: find_unanchored_period("change last year?", CAT)),
    ("anchor", "'this year'", True, lambda: find_unanchored_period("Bihar petrol this year?", CAT)),
    ("anchor", "anchored by explicit year", False,
     lambda: find_unanchored_period("in 2025-26 compared to previous year", CAT)),
    ("anchor", "no relative words", False, lambda: find_unanchored_period("Bihar petrol in 2019-20?", CAT)),
    ("anchor", "'last' about something else", False,
     lambda: find_unanchored_period("which state was last in diesel use in 2020-21?", CAT)),

    # ---- entity ambiguity --------------------------------------------------------------
    ("ambiguity", "uttar -> 2 states", True, lambda: find_ambiguity("Uttar's diesel in 2019-20?", CAT)),
    ("ambiguity", "pradesh -> 5 states", True, lambda: find_ambiguity("which Pradesh used most?", CAT)),
    ("ambiguity", "gujarat is unique", False, lambda: find_ambiguity("Gujarat diesel 2019-20?", CAT)),
    ("ambiguity", "bihar is unique", False, lambda: find_ambiguity("Bihar petrol 2024-25?", CAT)),
    ("ambiguity", "maharashtra is unique", False, lambda: find_ambiguity("Maharashtra petrol 2024-25?", CAT)),
    ("ambiguity", "'andaman' is unique despite 'daman'", False,
     lambda: find_ambiguity("Andaman petrol in 2024-25?", CAT)),
    ("ambiguity", "'goa' too short to be ambiguous", False,
     lambda: find_ambiguity("Goa petrol in 2024-25?", CAT)),
    # The three-way resolver: none / one / many (D54)
    ("ambiguity", "'pradesh' ties 5 states", True,
     lambda: find_ambiguity("which pradesh used the most petrol in 2024-25?", CAT)),
    ("ambiguity", "'Andhra Pradesh' spelled in full", False,
     lambda: find_ambiguity("Andhra Pradesh petrol in 2024-25?", CAT)),
    ("ambiguity", "'Uttar Pradesh' spelled in full", False,
     lambda: find_ambiguity("Uttar Pradesh diesel in 2019-20?", CAT)),
    ("ambiguity", "typo does not create a tie", False,
     lambda: find_ambiguity("Maharastra petrol in 2024-25?", CAT)),
    ("ambiguity", "'least' must not match EAST", False,
     lambda: find_ambiguity("which region used the least diesel in 2020-21?", CAT)),

    # ---- coverage: dropped filters -----------------------------------------------------
    ("coverage", "dropped year and product", True,
     lambda: coverage_gaps("Gujarat's diesel in 2019-20?", q([("state", "GUJARAT")]), CAT)),
    ("coverage", "complete plan", False,
     lambda: coverage_gaps("Gujarat's diesel in 2019-20?",
                           q([("state", "GUJARAT"), ("product", "HSD"), ("year", "2019-20")]), CAT)),
    ("coverage", "'which state' must not demand row_kind", False,
     lambda: coverage_gaps("Which state consumed the most petrol in 2024-25?",
                           q(ALL_MS_25, group_by=["state"]), CAT)),
    ("coverage", "abstain is exempt", False,
     lambda: coverage_gaps("anything", Abstain(kind="abstain", reason_code="no_such_column"), CAT)),

    # ---- overfilter: invented filters --------------------------------------------------
    ("overfilter", "invented a year", True,
     lambda: overfilter_gaps("Bihar's share of petrol?", q(BIHAR_MS_25), CAT)),
    ("overfilter", "typo 'Maharastra'", False,
     lambda: overfilter_gaps("Maharastra petrol in 2024-25?",
                             q([("state", "MAHARASHTRA"), ("product", "MS"), ("year", "2024-25")]), CAT)),
    ("overfilter", "typo 'Gujrat'", False,
     lambda: overfilter_gaps("Gujrat diesel in 2019-20?",
                             q([("state", "GUJARAT"), ("product", "HSD"), ("year", "2019-20")]), CAT)),
    ("overfilter", "typo 'Kerela'", False,
     lambda: overfilter_gaps("Kerela petrol in 2024-25?",
                             q([("state", "KERALA"), ("product", "MS"), ("year", "2024-25")]), CAT)),
    ("overfilter", "relative year reference", False,
     lambda: overfilter_gaps("Bihar petrol in 2025-26 vs previous year",
                             q([("state", "BIHAR"), ("product", "MS"), ("year", "2024-25")]), CAT)),
    ("overfilter", "alias petrol -> MS", False,
     lambda: overfilter_gaps("Bihar petrol in 2024-25?", q(BIHAR_MS_25), CAT)),
    ("overfilter", "compiler defaults exempt", False,
     lambda: overfilter_gaps("Bihar consumption in 2024-25?",
                             q([("state", "BIHAR"), ("product", "ALL"), ("row_kind", "entity"),
                                ("year", "2024-25")]), CAT)),
    # Adversarial: names that are genuinely close. A plain threshold waved these through.
    ("overfilter", "ANDHRA vs MADHYA (0.86 apart)", True,
     lambda: overfilter_gaps("Andhra Pradesh petrol in 2024-25?",
                             q([("state", "MADHYA PRADESH"), ("product", "MS"), ("year", "2024-25")]), CAT)),
    ("overfilter", "ANDHRA asked, ANDHRA used", False,
     lambda: overfilter_gaps("Andhra Pradesh petrol in 2024-25?",
                             q([("state", "ANDHRA PRADESH"), ("product", "MS"), ("year", "2024-25")]), CAT)),
    ("overfilter", "Chandigarh vs Chhattisgarh", True,
     lambda: overfilter_gaps("Chandigarh diesel in 2019-20?",
                             q([("state", "CHHATTISGARH"), ("product", "HSD"), ("year", "2019-20")]), CAT)),
    ("overfilter", "Jharkhand vs Uttarakhand", True,
     lambda: overfilter_gaps("Jharkhand diesel in 2019-20?",
                             q([("state", "UTTARAKHAND"), ("product", "HSD"), ("year", "2019-20")]), CAT)),
    ("overfilter", "short name GOA", False,
     lambda: overfilter_gaps("Goa petrol in 2024-25?",
                             q([("state", "GOA"), ("product", "MS"), ("year", "2024-25")]), CAT)),
    ("overfilter", "multi-word TAMIL NADU from 'tamil'", False,
     lambda: overfilter_gaps("tamil petrol in 2024-25?",
                             q([("state", "TAMIL NADU"), ("product", "MS"), ("year", "2024-25")]), CAT)),
    ("overfilter", "ampersand name J&K", False,
     lambda: overfilter_gaps("Jammu & Kashmir diesel in 2019-20?",
                             q([("state", "JAMMU & KASHMIR"), ("product", "HSD"), ("year", "2019-20")]), CAT)),
    ("overfilter", "wrong fuel", True,
     lambda: overfilter_gaps("Bihar petrol in 2024-25?",
                             q([("state", "BIHAR"), ("product", "HSD"), ("year", "2024-25")]), CAT)),
    ("overfilter", "wrong year", True,
     lambda: overfilter_gaps("Bihar petrol in 2024-25?",
                             q([("state", "BIHAR"), ("product", "MS"), ("year", "2019-20")]), CAT)),
    ("overfilter", "genuinely wrong state", True,
     lambda: overfilter_gaps("Bihar petrol in 2024-25?",
                             q([("state", "PUNJAB"), ("product", "MS"), ("year", "2024-25")]), CAT)),

    # ---- scope gap: aggregating across an unnamed dimension ----------------------------
    ("scope", "no year named", True,
     lambda: find_scope_gap("Bihar's share of consumption?", q([("state", "BIHAR")]), CAT)),
    ("scope", "year filtered", False,
     lambda: find_scope_gap("Bihar petrol 2024-25?", q(BIHAR_MS_25), CAT)),
    ("scope", "'all years' escape hatch", False,
     lambda: find_scope_gap("Bihar's share across all years?", q([("state", "BIHAR")]), CAT)),
    ("scope", "grouped by year", False,
     lambda: find_scope_gap("Bihar petrol by year?", q([("state", "BIHAR")], group_by=["year"]), CAT)),
    ("scope", "region unnamed must not fire", False,
     lambda: find_scope_gap("Gujarat's diesel in 2019-20?",
                            q([("state", "GUJARAT"), ("product", "HSD"), ("year", "2019-20")]), CAT)),

    # ---- derivation shape --------------------------------------------------------------
    ("shape", "divide with differing year", True,
     lambda: check_derivation_shape(d([("state", "BIHAR"), ("product", "MS"), ("year", "2025-26")],
                                      [("product", "MS"), ("year", "2024-25")]), CAT)),
    ("shape", "valid share", False,
     lambda: check_derivation_shape(d(BIHAR_MS_25, ALL_MS_25), CAT)),
    ("shape", "valid growth", False,
     lambda: check_derivation_shape(d([("state", "BIHAR"), ("product", "MS"), ("year", "2025-26")],
                                      [("state", "BIHAR"), ("product", "MS"), ("year", "2024-25")],
                                      op="percent_change"), CAT)),
    ("shape", "growth differing in state", True,
     lambda: check_derivation_shape(d([("state", "BIHAR"), ("product", "MS"), ("year", "2025-26")],
                                      [("state", "PUNJAB"), ("product", "MS"), ("year", "2025-26")],
                                      op="percent_change"), CAT)),
    ("shape", "growth with mismatched columns", True,
     lambda: check_derivation_shape(d([("state", "BIHAR"), ("product", "MS"), ("year", "2025-26")],
                                      [("state", "BIHAR"), ("year", "2024-25")],
                                      op="percent_change"), CAT)),
]


def raises(fn) -> bool:
    try:
        fn()
        return False
    except PlanFailure:
        return True


def _answer(text_unit, formatted="27,237.19"):
    """A minimal answered Answer, for the digit gate."""
    from answer import Answer, Citation, Value
    v = Value(raw=1.0, formatted=formatted, unit=text_unit,
              citations=[Citation(sheet="S", a1="A1", raw_value="1")])
    return Answer(question="which state was highest in 2025-26?", status="answered",
                  narration="GUJARAT used {v1}.", slots={"v1": v})


DIGIT_CASES = [
    # The unit itself can contain digits: PPAC's header cell reads "(`000 Metric Tonnes)". A unit
    # is a label copied from the file, not a computed figure, so its digits are backed. This fired
    # in the live UI and crashed the request.
    ("digits", "unit containing digits is not an unbacked number", False,
     lambda: check_digits(_answer("000 Metric Tonnes"), "which state was highest in 2025-26?")),
    ("digits", "a digit from nowhere still fires", True,
     lambda: check_digits(_fabricated(), "which state was highest?")),
]


def _fabricated():
    """An Answer whose narration smuggles in a number no slot backs."""
    from answer import Answer, Citation, Value
    v = Value(raw=1.0, formatted="27,237.19", unit="tonnes",
              citations=[Citation(sheet="S", a1="A1", raw_value="1")])
    return Answer(question="q", status="answered",
                  narration="GUJARAT used {v1}, up from 999.99 last year.", slots={"v1": v})


def raises_verification(fn) -> bool:
    """check_digits raises VerificationError, not PlanFailure — a different gate, a different
    exception. Sharing `raises` would have let a real failure escape as a crash."""
    from verify import VerificationError
    try:
        fn()
        return False
    except VerificationError:
        return True


COMPILE_CASES = [
    ("compile", "unknown column rejected", True,
     lambda: compile_plan(QueryPlan(kind="query", metric=Metric(agg="sum", column="tax"),
                                    filters=[], narration="{v1}"), CAT)),
    ("compile", "known column accepted", False, lambda: compile_plan(q(BIHAR_MS_25), CAT)),
    ("compile", "min+group_by+limit rejected", True,
     lambda: compile_plan(q(ALL_MS_25, group_by=["region"], agg="min", sort="asc", limit=1), CAT)),
    ("compile", "sum+group_by+limit fine", False,
     lambda: compile_plan(q(ALL_MS_25, group_by=["region"], sort="asc", limit=1,
                            narration="{v1} {v2}"), CAT)),
    ("compile", "state + all_india contradiction", True,
     lambda: compile_plan(q([("state", "BIHAR"), ("row_kind", "grand_total")]), CAT)),
    ("compile", "all_india alone is fine", False,
     lambda: compile_plan(q([("row_kind", "grand_total"), ("year", "2024-25")]), CAT)),
    ("compile", "narration hole mismatch", True,
     lambda: compile_plan(q(BIHAR_MS_25, narration="{v1} and {v2}"), CAT)),
    ("compile", "grouped narration needs two", False,
     lambda: compile_plan(q(ALL_MS_25, group_by=["state"], narration="{v1} {v2}"), CAT)),
]

if __name__ == "__main__":
    rows = [(g, n, want, bool(fn())) for g, n, want, fn in CASES]
    rows += [(g, n, want, raises(fn)) for g, n, want, fn in COMPILE_CASES]
    rows += [(g, n, want, raises_verification(fn)) for g, n, want, fn in DIGIT_CASES]

    passed, last = 0, None
    for gate, name, want, got in rows:
        if gate != last:
            print(f"\n── {gate}")
            last = gate
        ok = want == got
        passed += ok
        mark = "  ok  " if ok else " FAIL "
        print(f"{mark} {'fires' if want else 'quiet'}  {name}"
              + ("" if ok else f"   <- actually {'fired' if got else 'stayed quiet'}"))

    fires = sum(1 for *_, w, _ in [(r[0], r[1], r[2], r[3]) for r in rows] if w)
    print(f"\n{passed}/{len(rows)} passed   ({fires} should-fire, {len(rows) - fires} should-stay-quiet)")


# ── Conversational memory (D56) ────────────────────────────────────────────────
# Provenance, not lexical grounding. These run without a model, so the whole memory
# contract is testable for free.
from memory import Memory, provenance, STATED, INHERITED, DEFAULT, INVENTED   # noqa: E402
from verify import named_in                                                    # noqa: E402

T1 = q([("state", "GUJARAT"), ("product", "HSD"), ("year", "2019-20")])


def prov(question, plan, previous):
    return provenance(question, plan, previous, CAT,
                      lambda t, sibs, al=None: named_in(question, t, sibs, al))


MEMORY_CASES = [
    ("turn 1: everything stated",
     prov("What was Gujarat's diesel consumption in 2019-20?", T1, None),
     {"state": STATED, "product": STATED, "year": STATED}),

    ("follow-up: state stated, rest inherited",
     prov("What about Maharashtra?",
          q([("state", "MAHARASHTRA"), ("product", "HSD"), ("year", "2019-20")]), T1),
     {"state": STATED, "product": INHERITED, "year": INHERITED}),

    ("stated BEATS inherited: 'and petrol?' overrides carried diesel",
     prov("And petrol?",
          q([("state", "GUJARAT"), ("product", "MS"), ("year", "2019-20")]), T1),
     {"state": INHERITED, "product": STATED, "year": INHERITED}),

    ("compiler defaults are their own source",
     prov("What about Maharashtra?",
          q([("state", "MAHARASHTRA"), ("product", "ALL"), ("row_kind", "entity")]), T1),
     {"state": STATED, "product": DEFAULT, "row_kind": DEFAULT}),

    ("no memory: an unstated filter is still an invention",
     prov("What about Maharashtra?",
          q([("state", "MAHARASHTRA"), ("product", "HSD")]), None),
     {"state": STATED, "product": INVENTED}),

    ("inheriting a DIFFERENT value than the previous turn is an invention",
     prov("What about Maharashtra?",
          q([("state", "MAHARASHTRA"), ("year", "2011-12")]), T1),
     {"state": STATED, "year": INVENTED}),
]

if __name__ == "__main__":
    print("\n── memory / provenance")
    mp = 0
    for name, got, want in MEMORY_CASES:
        ok = got == want
        mp += ok
        print(("  ok   " if ok else " FAIL  ") + name + ("" if ok else f"\n        got {got}\n        want {want}"))

    m = Memory()
    m.remember("q1", T1); m.remember("q2", T1); m.remember("q3", T1)
    win = len(m.turns) == 2
    mp += win
    print(("  ok   " if win else " FAIL  ") + f"window holds 2 turns, not more (got {len(m.turns)})")

    m.clear()
    cleared = m.previous is None
    mp += cleared
    print(("  ok   " if cleared else " FAIL  ") + "clear() drops everything")

    print(f"\n{mp}/{len(MEMORY_CASES) + 2} memory cases passed")

    # Nonzero exit on any failure, so CI (or a pre-push hook) fails loudly instead of a green run
    # hiding a red line. `passed`/`rows` are globals from the first __main__ block above.
    sys.exit(0 if (passed == len(rows) and mp == len(MEMORY_CASES) + 2) else 1)
