"""End-to-end scoreboard. Tests what ships, not an internal component.

Every answered case is checked for lineage: at least one citation, and (where the case says so)
the exact cells. check_lineage inside execute() already asserts the cited cells reconstruct the
number, so a passing run also proves citations are complete, not merely present.
"""
import sys, pathlib, yaml
root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))
from ask import ask
from catalog import build
from planner import DeepSeekPlanner, get_planner

cases = yaml.safe_load((root / "questions.yaml").read_text())
from workbook import load                      # noqa: E402
_spec, db, _cat = load()
cat, planner = build(db), get_planner()
print(f"planner: {type(planner).__name__}\n")

passed = 0
for c in cases:
    print(f">>> {c['q']}")
    a = ask(c["q"], cat, db, planner)
    fails = []
    if a.status != c["status"]:
        fails.append(f"status={a.status} want {c['status']}")
    elif a.status == "answered":
        cits = [str(x) for x in a.all_citations()]
        if not cits:
            fails.append("answered with no citations")
        for want in c.get("cites", []):
            if want not in cits:
                fails.append(f"missing citation {want}; got {cits[:4]}")
        if "n_cites" in c and len(cits) != c["n_cites"]:
            fails.append(f"{len(cits)} citations, want {c['n_cites']}")
        # The number itself. Checking only status and citations let a wrong answer pass 9/9.
        for slot, want in (c.get("values") or {}).items():
            got = a.slots.get(slot)
            if got is None:
                fails.append(f"no slot {slot}")
            elif isinstance(want, (int, float)):
                try:
                    ok = abs(float(got.raw) - want) <= 0.01
                except (ValueError, TypeError):
                    ok = False          # a string where a number was expected is a mismatch, not a crash
                if not ok:
                    fails.append(f"{slot}={got.raw!r} want {want}")
            elif str(got.raw) != str(want):
                fails.append(f"{slot}={got.raw!r} want {want!r}")
    print(f"    {'PASS' if not fails else 'FAIL'}  {a.text()[:88]}")
    for f in fails:
        print("        " + f)
    passed += not fails

print(f"\n{passed}/{len(cases)} passed  |  API spend: ${DeepSeekPlanner.spend_usd:.6f}")
sys.exit(0 if passed == len(cases) else 1)
