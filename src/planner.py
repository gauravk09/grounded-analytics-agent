"""The one place a model runs.

Takes a question plus the catalog, returns a Plan (D5: a query or a refusal). Never returns a
number — the narration it writes carries holes that the engine fills later (D1).

Failure policy: constrained decoding, then Pydantic, then one repair retry, then abstain.
Model incompetence and missing data produce the same safe outcome — the system refuses, it
never answers wrongly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv

import ollama
from pydantic import ValidationError

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog import Catalog, build          # noqa: E402
from plan import Abstain, Plan, PlanEnvelope, PlanFailure  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MODEL = "llama3.2"

# Every entry here speaks the OpenAI chat-completions dialect, so one class serves them all.
# Adding a provider is a dict entry, not a new class (D22 — the seam is the validated plan,
# not the model). Prices are USD per million tokens.
PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "env": "DEEPSEEK_API_KEY",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "prices": {
            "deepseek-v4-flash": {"hit": 0.007, "miss": 0.22, "out": 0.66},
            "deepseek-v4-pro":   {"hit": 0.022, "miss": 0.66, "out": 1.98},
        },
        # Reasoning tokens are billed as output and dominated the bill; off by default.
        "extra_body": {"thinking": {"type": "disabled"}},
    },
    # Ollama Cloud — same OpenAI-compatible dialect, so it is a registry entry rather than a
    # new class (D39). Much larger models than fit locally, still behind the same validated-plan
    # seam (D22). Prices omitted deliberately: billing is plan-based rather than per-token, so
    # _bill() reports nothing instead of inventing a number (D26).
    "ollama-cloud": {
        "base_url": "https://ollama.com/v1",
        "env": "OLLAMA_API_KEY",
        # Fallback only. The real list is fetched live — hardcoding it was wrong within an hour
        # (none of the four names I first guessed existed).
        "models": ["gpt-oss:120b", "kimi-k3", "qwen3.5:397b", "glm-5.2"],
        "prices": {},
    },
    # OpenAI itself, and the template for any OpenAI-compatible endpoint. Adding Groq, Together,
    # Fireworks or a local vLLM is this same four-line entry with a different base_url/env — the
    # class never changes (D22). Models are fetched live from /models when a key is present, so the
    # fallback list only matters keyless. Prices omitted: they drift, and _bill reports nothing
    # rather than invent a number (D26).
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "env": "OPENAI_API_KEY",
        "models": ["gpt-4o-mini", "gpt-4o"],
        "prices": {},
    },
}
DEFAULT_PROVIDER = "deepseek"


def list_models(provider: str, api_key: str | None = None,
                allow_env: bool = True) -> list[str]:
    """Ask the provider what it serves, falling back to the registry if it will not say."""
    cfg = PROVIDERS[provider]
    key = (api_key or (os.environ.get(cfg["env"], "") if allow_env else "")).strip()
    if not key:
        return cfg["models"]
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=cfg["base_url"])
        found = sorted(m.id for m in client.models.list().data)
        return found or cfg["models"]
    except Exception:
        return cfg["models"]


def coverage_gaps(question: str, plan: Plan, catalog: Catalog, previous=None) -> list[str]:
    """Columns whose values the question names but the plan does not filter on.

    A mechanical proxy for "did the model read the whole question?". It catches a small model's
    characteristic failure — valid JSON with filters silently dropped — which schema validation
    cannot see. Conservative by design: it only fires when a catalog value is named *literally*
    or via a known alias, so it produces no false alarms on vague questions.
    """
    if plan.kind != "query":
        return []
    import re
    q = question.lower()

    def names(term: str) -> bool:
        # Word-boundary match. Plain substring matching is unsafe: "east" is inside "least".
        return re.search(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", q) is not None

    filtered = {f.column for f in plan.filters}
    default = catalog.defaults
    gaps = []
    for col in catalog.columns:
        if col.name in filtered or not col.labels:
            continue
        # A label equal to the compiler's default never counts. Otherwise "which STATE used
        # the most" demands a row_kind filter, because row_kind has a value called 'state' —
        # a collision between one column's name and another column's value.
        hit = any(names(lbl.lower()) and lbl != default.get(col.name) for lbl in col.labels)
        # An alias only counts if it maps to something other than the compiler's default —
        # the planner is not required to restate a value the compiler injects anyway.
        hit = hit or any(names(a) and v != default.get(col.name)
                         for a, v in col.value_aliases.items())
        if hit:
            gaps.append(col.name)
    return gaps


class CascadePlanner:
    """Try planners cheapest-first, escalating only on PlanFailure.

    A legitimate Abstain stops the cascade — the data genuinely cannot answer, and a stronger
    model would only cost money to reach the same conclusion.
    """

    def __init__(self, tiers: list):
        self.tiers = tiers

    ATTEMPTS = 3

    def plan(self, question: str, catalog: Catalog, memory=None) -> Plan:
        last = None
        clarify_col = None
        # The model is stochastic even at temperature 0, so a plan that trips a gate on one draw
        # often passes on the next. Retry the whole cascade a few times before abstaining, so a
        # one-off miss never surfaces as "I can't answer" — only a consistent failure abstains.
        # A legitimate Abstain (the model deciding the data can't answer) still returns immediately
        # via the `return plan` below, so this never spends retries on a real refusal.
        for attempt in range(self.ATTEMPTS):
            for tier in self.tiers:
                try:
                    plan = tier.plan(question, catalog, memory)
                except PlanFailure as e:
                    last = e
                    continue
                from verify import overfilter_gaps
                prev = memory.previous if memory else None
                cov = coverage_gaps(question, plan, catalog, prev)
                over = overfilter_gaps(question, plan, catalog, prev)
                gaps = cov + [f"invented {g}" for g in over]
                if gaps:
                    last = PlanFailure(f"{type(tier).__name__} ignored: {', '.join(gaps)}")
                    # An invented filter on a categorical column with a small label set is a value
                    # the model GUESSED (product=HSD for "diesel"), not a harmful hidden narrowing.
                    # When that's the only problem, we ask the user which value rather than refuse —
                    # the map/ask/abstain rule (D98). A dropped filter (coverage) is a different
                    # failure, so it clears the flag.
                    if over and not cov:
                        c = over[0].split("=", 1)[0]
                        clarify_col = c if catalog.labels_for(c) else None
                    elif cov:
                        clarify_col = None
                else:
                    # "A good plan" means "a plan that compiles". Compiling here is what turns a
                    # whitelist or slot-contract violation into an escalation instead of a crash.
                    try:
                        if plan.kind == "query":
                            from compile import compile_plan
                            compile_plan(plan, catalog)
                        elif plan.kind == "derived":
                            from compile import check_derived, compile_measure
                            check_derived(plan, catalog)
                            for m in plan.measures:
                                compile_measure(m, catalog)
                        return plan
                    except PlanFailure as e:
                        last = e
                if os.environ.get("PLANNER_TRACE"):
                    print(f"      [try {attempt + 1}] escalating past {type(tier).__name__}: {last}")
        # The model kept guessing a categorical value we can't verify (e.g. product=HSD for
        # "diesel"). Don't refuse — ask which. ask.py turns this into a clarify (D98).
        if clarify_col:
            return Abstain(kind="abstain", reason_code="ambiguous_value", detail=clarify_col)
        # Not "not a data question" — that blames the file for our planner giving up (D49).
        return Abstain(kind="abstain", reason_code="planner_failed", detail=str(last)[:160])

    def chat_json(self, system: str, user: str) -> dict | None:
        """Free-form JSON from the configured model — for deck narrative/arrangement, not planning.

        Same cheapest-first spirit as plan(): try each tier, return the first that answers. Returns
        None if none can, so the caller (deck_agent) keeps its deterministic fallback. This is why
        a deck authored via /api/deck now uses the caller's provider, not a hardcoded llama3.2 (D92).
        """
        for tier in self.tiers:
            try:
                out = tier.chat_json(system, user)
                if out is not None:
                    return out
            except Exception:
                continue
        return None


def make_planner(provider: str | None = DEFAULT_PROVIDER, model: str | None = None,
                 api_key: str | None = None, local_first: bool = True,
                 allow_env: bool = True) -> "Planner":
    """Build the cascade explicitly. Used by the app; get_planner() is the CLI default.

    Always returns a CascadePlanner even for one tier — the coverage and compile gates live
    inside it, and they are quality checks rather than routing details (D28).
    """
    tiers = []
    if local_first:
        tiers.append(OllamaPlanner())
    if provider:
        tiers.append(DeepSeekPlanner(model=model, provider=provider, api_key=api_key,
                                     allow_env=allow_env))
    if not tiers:
        raise RuntimeError("no planner tier selected")
    return CascadePlanner(tiers)


def get_planner() -> "Planner":
    """DeepSeek when a key is present, local Ollama otherwise. The hybrid from D4."""
    tiers = [OllamaPlanner()]                                  # free, local
    # PLANNER_PROVIDER / PLANNER_MODEL let a run swap the paid tier without touching code —
    # the point of the registry (D39). Defaults to DeepSeek.
    provider = os.environ.get("PLANNER_PROVIDER", DEFAULT_PROVIDER)
    if provider in PROVIDERS and os.environ.get(PROVIDERS[provider]["env"], "").strip():
        tiers.append(DeepSeekPlanner(model=os.environ.get("PLANNER_MODEL") or None,
                                     provider=provider))
    # Always wrapped, even for a single tier: coverage_gaps is a quality gate, not a routing
    # detail (D28). Unwrapped, an under-filtered plan would be returned as an answer whenever
    # no API key happened to be set.
    return CascadePlanner(tiers)

RULES = """You translate a question about a data table into a query plan. You never compute.

Rules, in order of importance:
1. Never write a digit in `narration`. Put a hole where a RESULT goes and write a full sentence
   around it. A hole is ONLY for a value the query returns: the metric, and any group_by label.
   Filter values are written out as words, not as holes.
   No group_by -> exactly one hole {v1}. One group_by -> exactly two, {v1} the label and {v2}
   the number.
2. Add one filter for EVERY condition named in the question. A question naming three things
   needs three filters.
3. Use only the column names and values listed in the catalog. Never invent one.
4. "most" and "least" refer to the group TOTAL. Keep agg "sum" and let sort+limit pick the
   extreme group. Never use agg min or max together with group_by — that ranks groups by their
   smallest or largest single row, which is a different and almost always wrong question.
5. "Which X is highest/lowest/most/least" means: group_by exactly that X, set `sort` to desc or
   asc, and set `limit` to 1. Otherwise leave sort "none" and limit 0.
6. Leave `group_by` empty unless the question asks for a breakdown ("by X", "each X",
   "which X"). A question about one specific thing has no group_by.
7. A growth / increase / change / "compared to" question is kind "derived" with op
   "percent_change": the two measures are IDENTICAL except for the period. scale 100.
8. A "share", "proportion" or "percentage of" question is kind "derived": two measures and a
   divide with scale 100. The denominator repeats the numerator's filters MINUS the one
   dimension being shared over. A derived narration has exactly one hole, {v1}.
9. If the question cannot be answered from the catalog, abstain by choosing a code and naming
   the column or value at issue. Do NOT write an explanation — we compose that from the
   catalog. Codes:
     no_such_column       - the concept is absent entirely. detail = the missing concept.
     value_not_in_column  - the column exists but that value does not. detail = the column name.
     not_a_data_question  - no metric can be extracted at all. detail = "".
"""

CLOSE = "\nReturn only the JSON object."


def worked_examples(catalog: Catalog) -> str:
    """Few-shot examples built from THIS catalog.

    They used to be four hand-written PPAC queries. That taught every model that rows are states
    and that `product` exists — true of one file and misleading for every other. Examples are the
    strongest signal in a prompt, so a hardcoded one is not a cosmetic problem: on a workbook of
    budget lines, the model would be shown a schema that is not the schema it has.

    Generated from real column names and real labels, so the shapes are demonstrated without
    asserting anything false. If a catalog is too thin to build an example (no labels anywhere),
    we emit fewer examples rather than inventing values — the rules alone still specify the
    contract.
    """
    ent, per, meas = catalog.entity, catalog.period, catalog.measure
    unit = f" {catalog.unit}" if catalog.unit else ""

    def labels(col):
        return catalog.labels_for(col) or []

    e_lab = (labels(ent) or [None])[0]
    periods = labels(per)
    p1, p2 = (periods[-1], periods[-2]) if len(periods) >= 2 else (None, None)

    # A record workbook has no period: examples demonstrate the entity + a measure, not year-over
    # -year. Showing the crosstab shapes here would teach a schema this file does not have.
    if not periods:
        meas_cols = [c.name for c in catalog.columns
                     if c.dtype in ("DOUBLE", "BIGINT") and c.name != "row_kind"]
        m = meas_cols[0] if meas_cols else meas
        rex = ["\nExamples, using this file's own columns and values."]
        rex.append(f'''
Question: which {ent} has the highest {m}?
{{"plan": {{"kind": "query",
  "metric": {{"agg": "sum", "column": {json.dumps(m, ensure_ascii=False)}}},
  "filters": [], "group_by": [{json.dumps(ent, ensure_ascii=False)}],
  "sort": "desc", "limit": 1,
  "narration": "{{v1}} has the highest {m}, at {{v2}}."}}}}''')
        if e_lab:
            rex.append(f'''
Question: what is {e_lab}\'s {m}?
{{"plan": {{"kind": "query",
  "metric": {{"agg": "sum", "column": {json.dumps(m, ensure_ascii=False)}}},
  "filters": [{{"column": {json.dumps(ent, ensure_ascii=False)}, "op": "=", "value": {json.dumps(str(e_lab), ensure_ascii=False)}}}],
  "group_by": [], "narration": "{e_lab} is at {{v1}} for {m}."}}}}''')
        return "\n".join(rex)

    # A third dimension, if the file has one: the labelled column with the FEWEST distinct values.
    # Fewest is the right test — a small closed set (fuel type, category) is a real dimension you
    # filter on, while a large one is usually derivable from the entity itself. Taking the first
    # labelled column instead picked `region` for the fuel file, which is implied by the state and
    # therefore a redundant filter to demonstrate.
    others = [c for c in catalog.columns
              if c.name not in (ent, per, meas, "row_kind") and c.labels]
    other = min(others, key=lambda c: len(c.labels)).name if others else None
    o_lab = (labels(other) or [None])[0] if other else None
    # Something to group BY that is not the thing being filtered.
    group = next((c.name for c in catalog.columns
                  if c.name not in (per, meas, "row_kind", other) and c.labels), ent)

    def flt(pairs):
        # json.dumps, not %s. The Union Budget's period labels are Hindi and English in one cell
        # with embedded newlines — pasted raw they produce a prompt full of broken JSON, which is
        # the worst possible thing to show a model that must reply in JSON.
        return ", ".join(
            '{"column": %s, "op": "=", "value": %s}' % (json.dumps(c, ensure_ascii=False), json.dumps(str(v), ensure_ascii=False))
            for c, v in pairs if v is not None)

    def lit(v):
        """A label inside prose. Same escaping problem, without the surrounding quotes."""
        return json.dumps(str(v), ensure_ascii=False)[1:-1]

    out = ["\nExamples, using this file's own columns and values."]

    if e_lab and p1:
        pairs = [(ent, e_lab), (other, o_lab), (per, p1)]
        out.append(f'''
Question: what was {lit(e_lab)} in {lit(p1)}?
{{"plan": {{"kind": "query",
  "metric": {{"agg": "sum", "column": "{meas}"}},
  "filters": [{flt(pairs)}],
  "group_by": [],
  "narration": "{lit(e_lab)} was {{v1}}{unit} in {lit(p1)}."}}}}''')

    if p1:
        pairs = [(other, o_lab), (per, p1)]
        out.append(f'''
Question: which {group} was highest in {lit(p1)}?
{{"plan": {{"kind": "query",
  "metric": {{"agg": "sum", "column": "{meas}"}},
  "filters": [{flt(pairs)}],
  "group_by": ["{group}"], "sort": "desc", "limit": 1,
  "narration": "{{v1}} was highest in {lit(p1)}, at {{v2}}{unit}."}}}}''')

    if e_lab and p1 and p2:
        a = flt([(ent, e_lab), (other, o_lab), (per, p1)])
        b = flt([(ent, e_lab), (other, o_lab), (per, p2)])
        out.append(f'''
Question: how much did {lit(e_lab)} change in {p1} compared with {lit(p2)}?
{{"plan": {{"kind": "derived",
  "measures": [
    {{"name": "m1", "agg": "sum", "column": "{meas}", "filters": [{a}]}},
    {{"name": "m2", "agg": "sum", "column": "{meas}", "filters": [{b}]}}],
  "derive": {{"op": "percent_change", "of": "m1", "by": "m2", "scale": 100}},
  "narration": "{lit(e_lab)} changed by {{v1}}% in {p1} versus {lit(p2)}."}}}}''')

        num = flt([(ent, e_lab), (other, o_lab), (per, p1)])
        den = flt([(other, o_lab), (per, p1)])       # the shared-over dimension is dropped
        out.append(f'''
Question: what share of the total was {lit(e_lab)} in {lit(p1)}?
{{"plan": {{"kind": "derived",
  "measures": [
    {{"name": "m1", "agg": "sum", "column": "{meas}", "filters": [{num}]}},
    {{"name": "m2", "agg": "sum", "column": "{meas}", "filters": [{den}]}}],
  "derive": {{"op": "divide", "of": "m1", "by": "m2", "scale": 100}},
  "narration": "{lit(e_lab)} accounted for {{v1}}% of the total in {lit(p1)}."}}}}''')

    if catalog.absent:
        out.append(f'''
Question: how much {catalog.absent[0]} was there in {p1 or "that period"}?
{{"plan": {{"kind": "abstain", "reason_code": "no_such_column",
  "detail": "{catalog.absent[0]}"}}}}''')

    out.append('''
Question: what was it in a period the file does not cover?
{"plan": {"kind": "abstain", "reason_code": "value_not_in_column", "detail": "%s"}}''' % per)

    return "\n".join(out)


def system_prompt(catalog: Catalog) -> str:
    return RULES + worked_examples(catalog) + CLOSE


class Planner(Protocol):
    def plan(self, question: str, catalog: Catalog) -> Plan: ...


class DeepSeekPlanner:
    """DeepSeek via its OpenAI-compatible API.

    The key is read from the DEEPSEEK_API_KEY environment variable, loaded from .env
    (gitignored). It is never hardcoded, logged, or included in a prompt.

    DeepSeek supports JSON-object mode but not full JSON-Schema constrained decoding, so the
    schema goes in the prompt and Pydantic remains the enforcer. The failure policy is
    unchanged: validate, retry once, then abstain.
    """

    spend_usd = 0.0     # class-level running total for the process

    def __init__(self, model: str | None = None, retries: int = 1, thinking: bool = False,
                 provider: str = DEFAULT_PROVIDER, api_key: str | None = None,
                 allow_env: bool = True):
        from openai import OpenAI          # imported lazily so Ollama-only runs need no openai
        cfg = PROVIDERS[provider]
        # Explicit key (e.g. typed into the UI, session-only) wins; otherwise the environment,
        # loaded from .env. Never hardcoded, never logged, never put in a prompt.
        # allow_env=False is the demo path: the key must be supplied explicitly, so nothing
        # runs on a credential the reviewer did not enter.
        key = (api_key or (os.environ.get(cfg["env"], "") if allow_env else "")).strip()
        if not key:
            raise RuntimeError(
                f"{cfg['env']} is empty. Paste your key into .env (see .env.example) "
                f"or enter it in the app sidebar. Do not put it in source."
            )
        self.provider = provider
        self.client = OpenAI(api_key=key, base_url=cfg["base_url"])
        self.model = model or cfg["models"][0]
        self.PRICES = cfg["prices"]
        self.retries = retries
        self.extra = {} if thinking else dict(cfg.get("extra_body", {}))

    def plan(self, question: str, catalog: Catalog, memory=None) -> Plan:
        schema = json.dumps(PlanEnvelope.model_json_schema())
        # Memory goes AFTER the catalog and before the question, so the stable prefix
        # (system + catalog) still caches (D26).
        ctx = memory.context_for_prompt() if memory else ""
        messages = [
            {"role": "system", "content": system_prompt(catalog)
                            + f"\n\nYour reply must match this JSON Schema:\n{schema}"},
            {"role": "user", "content": f"{catalog.to_prompt()}\n\n{ctx}\n\nQuestion: {question}"},
        ]
        for attempt in range(self.retries + 1):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0,
                extra_body=self.extra,
            )
            self._bill(resp.usage)
            raw = resp.choices[0].message.content
            try:
                return PlanEnvelope.model_validate_json(raw).plan
            except ValidationError as e:
                if attempt == self.retries:
                    break
                messages += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"That failed validation:\n{e}\nReturn corrected JSON."},
                ]
        raise PlanFailure(f"{self.model}: no valid plan after {self.retries + 1} attempts")

    def chat_json(self, system: str, user: str) -> dict | None:
        """A single free-form JSON turn on this provider — same client, key and billing as plan()."""
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0,
            extra_body=self.extra,
        )
        self._bill(resp.usage)
        return json.loads(resp.choices[0].message.content)

    def _bill(self, usage) -> None:
        """Measure spend rather than estimate it. DeepSeek reports cache hits separately."""
        pr = self.PRICES.get(self.model)
        if not pr or usage is None:
            return
        hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
        miss = getattr(usage, "prompt_cache_miss_tokens", None)
        if miss is None:
            miss = (usage.prompt_tokens or 0) - hit
        cost = (hit * pr["hit"] + miss * pr["miss"] + (usage.completion_tokens or 0) * pr["out"]) / 1e6
        DeepSeekPlanner.spend_usd += cost
        if os.environ.get("PLANNER_TRACE"):
            print(f"      {self.model}: {hit} cached + {miss} fresh in, "
                  f"{usage.completion_tokens} out = ${cost:.6f}")


class OllamaPlanner:
    def __init__(self, model: str = MODEL, retries: int = 1):
        self.model = model
        self.retries = retries

    def plan(self, question: str, catalog: Catalog, memory=None) -> Plan:
        ctx = memory.context_for_prompt() if memory else ""
        prompt = f"{catalog.to_prompt()}\n\n{ctx}\n\nQuestion: {question}"
        messages = [{"role": "system", "content": system_prompt(catalog)},
                    {"role": "user", "content": prompt}]

        for attempt in range(self.retries + 1):
            raw = ollama.chat(
                model=self.model,
                messages=messages,
                format=PlanEnvelope.model_json_schema(),
                options={"temperature": 0},   # same question, same plan, every time
            )["message"]["content"]

            try:
                return PlanEnvelope.model_validate_json(raw).plan
            except ValidationError as e:
                if attempt == self.retries:
                    break
                # Show the model its own error and let it try once more.
                messages += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"That failed validation:\n{e}\nReturn corrected JSON."},
                ]

        raise PlanFailure(f"{self.model}: no valid plan after {self.retries + 1} attempts")

    def chat_json(self, system: str, user: str) -> dict | None:
        """A single free-form JSON turn on the local model — the deck fallback path when no paid
        tier is configured. Returns None on any failure so the caller keeps its deterministic story."""
        raw = ollama.chat(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            format="json",
            options={"temperature": 0},
        )["message"]["content"]
        return json.loads(raw)


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    from workbook import load
    _spec, _db, cat = load()
    planner = get_planner()
    print(f"[planner: {type(planner).__name__}]")

    for q in sys.argv[1:] or ["What was Gujarat's diesel consumption in 2019-20?"]:
        p = planner.plan(q, cat)
        print(f"\nQ: {q}")
        print(json.dumps(p.model_dump(), indent=2))
