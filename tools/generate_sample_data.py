"""Regenerates the sample data in data/ from the red-team test suite workbook.

Usage: python3 tools/generate_sample_data.py path/to/Sample_Test_Suite.xlsx data/

Severity, likelihood and detectability come from the workbook's Test Cases sheet. Everything else
(sample results, the inspection simulation, the snowball case, the quality series and the reviewer
experiment) is synthesized and illustrative. Every modelling choice is a named constant in the
ASSUMPTIONS block below. The random seed is fixed, so the output is repeatable.

Model, per Monte Carlo run:
  1. Injection: each agent output carries an error with probability ERROR_RATE. Each error is
     assigned an agent failure type (a test ID from the suite) and takes that test's RPN and error
     type (Reasoning / Planning / System execution). Human-review cases (H1-H8) are the control
     layer: they are never injected and their RPNs are never used as risk priors.
  2. Selection: a budget of k outputs is chosen, either at random or by the guardrail risk score.
  3. Review: the selected outputs are reviewed in shuffled order (the reviewer never sees the risk
     score, so the reviewer model and UI are identical for both strategies). Each error is caught
     with probability BASE_CATCH x fatigue x load x plausibility.

Team-added failure modes (TEAM_ADDED) join the workbook's test cases without editing the workbook.
In the strategy simulation only, Lopa's priority failure modes (LOPA_MODES) are topped up to at least
MIN_INJECTED_PER_MODE injected errors per run. The quality series uses the natural mix and seeds
CANARY_PER_DAY known errors into each day's random review slice to estimate reviewer sensitivity.

Needs: pip install openpyxl
"""

from __future__ import annotations

import bisect
import json
import logging
import math
import random
import sys
from pathlib import Path

import openpyxl

log = logging.getLogger("generate_sample_data")

# ============================== ASSUMPTIONS ==============================
# Every constant below is an illustrative assumption until the experiment has run. The Method tab
# lists them (they are written to data/sim_meta.json together with these comments).
SEED = 83
N_RUNS = 500  # Monte Carlo runs; intervals are the middle 95% of runs

# Definition, not an assumption: an injected error is high-impact when its failure type has
# RPN >= HIGH_IMPACT_RPN. 60 is the Critical tier. Change it here only, then regenerate.
HIGH_IMPACT_RPN = 60

ASSUMPTIONS = [
    # (name, value, unit, comment)
    ("N_OUTPUTS", 2000, "agent answers", "ASSUMPTION: answers produced in one simulated period"),
    ("PERIOD_DAYS", 5, "business days", "ASSUMPTION: length of the simulated period (400 answers a day)"),
    ("ERROR_RATE", 0.065, "share", "ASSUMPTION: share of answers that contain an injected error"),
    ("INJECTION_WEIGHT", "likelihood score", "", "ASSUMPTION: a failure type is injected in proportion to its likelihood score (1-5)"),
    ("BASE_CATCH", 0.62, "probability", "ASSUMPTION: chance a reviewer catches an error they look at, before modifiers"),
    ("FATIGUE_ONSET", 10, "clean items", "ASSUMPTION: fatigue starts after this many consecutive clean items"),
    ("FATIGUE_FULL", 50, "clean items", "ASSUMPTION: fatigue is at its maximum after this many consecutive clean items"),
    ("FATIGUE_MAX_DROP", 0.35, "share", "ASSUMPTION: at full fatigue the catch chance falls by 35%"),
    ("TEAM_CAPACITY", 50, "reviews a day", "ASSUMPTION: reviews the team can do in a day without loss of quality"),
    ("LOAD_DROP", 0.25, "share per capacity", "ASSUMPTION: each extra team-capacity of daily reviews cuts the catch chance by 25%"),
    ("LOAD_FLOOR", 0.5, "share", "ASSUMPTION: load never cuts the catch chance below half"),
    ("PLAUSIBLE_MULT", 0.55, "multiplier", "ASSUMPTION: catch chance is multiplied by 0.55 when the wrong output looks plausible"),
    ("PLAUSIBLE_BY_DETECTABILITY", {1: 0.0, 2: 0.0, 3: 0.1, 4: 0.3, 5: 0.6}, "probability", "ASSUMPTION: chance an error looks plausible, by the failure type's detectability score"),
    ("GUARDRAIL_STRENGTH", 1.0, "multiplier", "ASSUMPTION: scales how much the guardrail risk score separates errors from clean answers"),
    ("GUARDRAIL_SIGNAL", {"System execution": 1.9, "Reasoning": 1.2, "Planning": 0.7}, "score shift", "ASSUMPTION: mean risk-score shift for an error, by error type (clean answers score N(0,1)); planning errors look normal to most guardrails"),
    ("GUARDRAIL_SEVERITY_SLOPE", 0.15, "score per severity point", "ASSUMPTION: extra risk-score shift per severity point"),
    ("MIN_PER_REVIEW", 4.5, "minutes", "ASSUMPTION: reviewer time per answer"),
    ("DEFAULT_BUDGET", 10, "% of answers", "ASSUMPTION: current review budget"),
    ("SNOWBALL_HOUSEHOLDS", 412, "households", "ASSUMPTION: households holding the fund with the stale price"),
    ("SNOWBALL_ANSWERS", 268, "answers", "ASSUMPTION: agent answers that use the stale price during the day"),
    ("SNOWBALL_DAY_MIN", 480, "minutes", "ASSUMPTION: the trading day; answers arrive evenly at random through it, the stale feed starts at minute 0"),
    ("ROOT_CAUSE_CLUSTER", 3, "flagged answers", "ASSUMPTION: risk-based review links the catches to the feed once this many answers with a freshness flag on the same fund have been reviewed"),
    ("INVESTIGATION_MIN", 30, "minutes", "ASSUMPTION: time from the cluster to blocking further answers that use the feed"),
    ("QUALITY_DAYS", 14, "days", "ASSUMPTION: length of the quality-monitoring window"),
    ("QUALITY_SLICE", 40, "answers a day", "ASSUMPTION: size of the daily random review slice used to estimate quality"),
    ("DRIFT_START_DAY", 9, "day", "ASSUMPTION: the true error rate starts to rise on this day"),
    ("DRIFT_PER_DAY", 0.02, "share a day", "ASSUMPTION: rise in the true error rate per day after drift starts"),
    ("ALERT_LIMIT", 0.08, "share", "ASSUMPTION: alert limit on the estimated error rate"),
    ("ALERT_DAYS", 2, "days", "ASSUMPTION: alert when the estimate is above the limit this many days in a row"),
    ("BANNER_MULT", 1.25, "multiplier", "ASSUMPTION (reviewer experiment only): a coverage and freshness banner raises the catch chance by 25%"),
    ("ALERT_FATIGUE_MULT", 0.6, "multiplier", "ASSUMPTION (reviewer experiment only): alerts on 40% of items cut the catch chance by 40%"),
    ("EXPERIMENT_ITEMS", 60, "items per condition", "ASSUMPTION: planted errors shown per condition in the reviewer experiment"),
    ("EXPERIMENT_REVIEWERS", 12, "reviewers", "ASSUMPTION: reviewers in the reviewer experiment"),
    ("T1", "S4 L3 D4 (RPN 48)", "scores", "ASSUMPTION: team-added failure mode 'Wrong tool selected' (Planning), not in the workbook; team-added, pending Lopa's review"),
    ("LOPA_MODES", {"Calculation": "C1 C2 C6", "Intent": "I1 I2", "Wrong tool": "T1"}, "test IDs", "Lopa's three priority failure modes"),
    ("MIN_INJECTED_PER_MODE", 15, "errors per run", "ASSUMPTION (strategy simulation only): each of Lopa's modes is oversampled to at least this many injected errors per run, by reassigning errors from the most common other failure types so the total stays the same. The quality series is not oversampled"),
    ("CANARY_PER_DAY", 4, "seeded errors a day", "ASSUMPTION: known errors seeded into each day's random review slice, drawn from the natural error mix and reviewed like any other answer. More than the suite's 1 in 50 so the reviewer catch rate can be estimated, at the cost of fewer real answers reviewed"),
    ("CORRECTION_METHOD", "raw rate / sensitivity", "", "Corrected error rate = errors found in the non-canary reviews / non-canary reviews, divided by reviewer sensitivity (canaries caught / canaries seen to date), capped at 100%. 95% interval: 2.5th-97.5th percentile of CORRECTION_DRAWS draws of Beta(found+1, misses+1) / Beta(canaries caught+1, canaries missed+1)"),
    ("CORRECTION_DRAWS", 2000, "draws", "Monte Carlo draws for the corrected-estimate interval"),
]
A = {name: value for name, value, _, _ in ASSUMPTIONS}

BUDGETS = list(range(2, 31))  # % of answers reviewed
CATCH_RATES = [0.40, 0.50, 0.62, 0.75, 0.90]
METHODS = ["Random sampling", "Risk-based"]
ERROR_TYPES = ["Reasoning", "Planning", "System execution"]
CONTROL_LAYER = "Control layer (human review)"
ILLUSTRATIVE_EVIDENCE = "Illustrative sample result; not yet run"

# Failure modes the team added that are not in the workbook. Scored and tiered like the rest.
TEAM_ADDED = [dict(
    id="T1", layer="Agent", category="Tool selection", test="Wrong tool selected", error_type="Planning",
    setup="The agent calls the wrong tool for the question, for example a price-lookup tool for a "
          "performance question, or the wrong account API",
    expected="Answer built on the wrong tool's output",
    pass_criteria="The agent selects the right tool or flags uncertainty",
    control="", exposure="", severity=4, likelihood=3, detectability=4,
    team_note="Team-added, pending Lopa's review",
)]
LOPA_MODES = {name: ids.split() for name, ids in A["LOPA_MODES"].items()}

ERROR_TYPE = {}
for k in "D1 D2 D3 D4 D5 D6 D7 C5 S2 S4".split():
    ERROR_TYPE[k] = "System execution"
for k in "C4 C6 I1 I2 I4 I5 I6 O5 S1".split():
    ERROR_TYPE[k] = "Planning"
for t in TEAM_ADDED:
    ERROR_TYPE[t["id"]] = t["error_type"]
for k in "C1 C2 C3 C7 I3 O1 O2 O3 O4 O6 S3".split():
    ERROR_TYPE[k] = "Reasoning"
MVP = {
    "D1": "Snowball root cause", "C6": "Calculation error", "I1": "Intent misread",
    "C4": "Wrong tool selected", "D2": "Coverage gap", "H5": "Plausibility anchoring",
}


def tier(rpn: int) -> str:
    return "Critical" if rpn >= 60 else "High" if rpn >= 36 else "Medium" if rpn >= 18 else "Low"


def summarize(values: list[float]) -> list[int]:
    """Integer mean and the middle-95% interval of the Monte Carlo runs."""
    s = sorted(values)
    lo = s[int(0.025 * (len(s) - 1))]
    hi = s[math.ceil(0.975 * (len(s) - 1))]
    return [round(sum(s) / len(s)), round(lo), round(hi)]


def share_summary(num: list[int], den: list[int]) -> list[float]:
    """Mean share (ratio of means) and middle-95% interval of per-run shares."""
    per_run = sorted(n / d if d else 0.0 for n, d in zip(num, den))
    lo = per_run[int(0.025 * (len(per_run) - 1))]
    hi = per_run[math.ceil(0.975 * (len(per_run) - 1))]
    return [round(sum(num) / max(1, sum(den)), 4), round(lo, 4), round(hi, 4)]


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials (same formula as js/shared.js)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


# ------------------------------ reviewer model ------------------------------
def fatigue_factor(clean_streak: int) -> float:
    span = A["FATIGUE_FULL"] - A["FATIGUE_ONSET"]
    level = min(1.0, max(0, clean_streak - A["FATIGUE_ONSET"]) / span)
    return 1 - A["FATIGUE_MAX_DROP"] * level


def load_factor(daily_reviews: float) -> float:
    over = max(0.0, daily_reviews / A["TEAM_CAPACITY"] - 1)
    return max(A["LOAD_FLOOR"], 1 - A["LOAD_DROP"] * over)


def catch_prob(base: float, clean_streak: int, daily_reviews: float, plausible: bool) -> float:
    """The one reviewer model, used for every strategy."""
    p = base * fatigue_factor(clean_streak) * load_factor(daily_reviews)
    return p * (A["PLAUSIBLE_MULT"] if plausible else 1.0)


def review_queue(rng: random.Random, k: int, errors: list[dict], base_rates: list[float],
                 daily: float) -> dict[float, list[bool]]:
    """Places the selected errors at random positions in a shuffled queue of k items and returns,
    per base catch rate, whether each error was caught. Streak = clean items reviewed just before."""
    m = len(errors)
    positions = sorted(rng.sample(range(k), m))
    order = errors[:]
    rng.shuffle(order)
    prev = -1
    probs = []
    for pos, e in zip(positions, order):
        streak = pos - prev - 1
        prev = pos
        probs.append((e, fatigue_factor(streak) * load_factor(daily)
                      * (A["PLAUSIBLE_MULT"] if e["plausible"] else 1.0)))
    out = {}
    for b in base_rates:
        out[b] = {id(e): e["u"] < b * f for e, f in probs}
    return out


# ------------------------------ register ------------------------------
def register_rows(ws) -> list[tuple]:
    """Workbook test cases followed by the team-added ones, as uniform tuples."""
    out = []
    for r in ws.iter_rows(min_row=5, values_only=True):
        if not r[0]:
            continue
        rid, layer, cat, test, setup, _prompt, exp, passc, ctrl, s, l, d = r[:12]
        out.append((rid, layer, cat, test, setup, exp, passc, ctrl, s, l, d, r[14], ""))
    for t in TEAM_ADDED:
        out.append((t["id"], t["layer"], t["category"], t["test"], t["setup"], t["expected"],
                    t["pass_criteria"], t["control"], t["severity"], t["likelihood"],
                    t["detectability"], t["exposure"], t["team_note"]))
    return out


def build_register(ws, rng: random.Random) -> list[dict]:
    rows = []
    for rid, layer, cat, test, setup, exp, passc, ctrl, s, l, d, reg, note in register_rows(ws):
        rpn = s * l * d
        is_agent = rid in ERROR_TYPE
        # Illustrative sample result: failure more likely at high RPN. Not a real run.
        u = rng.random()
        p_fail = min(0.85, rpn / 110)
        if u < 0.12:
            status, result = "Not started", "Not run"
        elif u < 0.12 + 0.88 * p_fail:
            status, result = "Done", "Fail"
        elif u < 0.12 + 0.88 * (p_fail + (1 - p_fail) * 0.35):
            status, result = "Done", "Partial"
        else:
            status, result = "Done", "Pass"
        rows.append(dict(
            id=rid, layer=layer, category=cat, test=test,
            area=ERROR_TYPE[rid] if is_agent else CONTROL_LAYER,
            is_agent=is_agent, severity=s, likelihood=l, detectability=d, rpn=rpn, tier=tier(rpn),
            high_impact=bool(is_agent and rpn >= HIGH_IMPACT_RPN),
            status=status, result=result, result_illustrative=True, what=exp,
            evidence=ILLUSTRATIVE_EVIDENCE, control=ctrl, exposure=reg, mvp=MVP.get(rid, ""),
            setup=setup, pass_criteria=passc, team_added=bool(note), team_note=note,
        ))
    return rows


# ------------------------------ inspection Monte Carlo ------------------------------
MODE_OF = {tid: name for name, ids in LOPA_MODES.items() for tid in ids}


def top_up_modes(types: list[dict], agent: list[dict], rng: random.Random) -> None:
    """Oversamples Lopa's modes to MIN_INJECTED_PER_MODE errors (strategy simulation only) by
    reassigning errors from the most common non-Lopa failure type, so the total is unchanged."""
    for name, ids in LOPA_MODES.items():
        pool = [t for t in agent if t["id"] in ids]
        weights = [t["likelihood"] for t in pool]
        have = sum(1 for t in types if MODE_OF.get(t["id"]) == name)
        for _ in range(max(0, A["MIN_INJECTED_PER_MODE"] - have)):
            counts: dict[str, int] = {}
            for t in types:
                if t["id"] not in MODE_OF:
                    counts[t["id"]] = counts.get(t["id"], 0) + 1
            if not counts:
                return
            most = max(sorted(counts), key=lambda k: counts[k])
            idx = next(i for i, t in enumerate(types) if t["id"] == most)
            types[idx] = rng.choices(pool, weights)[0]


def mean(v: list[float]) -> float:
    return sum(v) / len(v)


def apportion(raw: list[float], total: int, cap: list[int] | None = None) -> list[int]:
    """Largest-remainder rounding: integers close to raw that sum exactly to total, and (when cap
    is given) never above cap; any excess moves to the entry with the most room."""
    out = _apportion(raw, total)
    if cap is not None:
        for i in range(len(out)):
            while out[i] > cap[i]:
                j = max(range(len(out)), key=lambda j: (cap[j] - out[j], raw[j] - out[j]))
                if cap[j] - out[j] <= 0:
                    break
                out[i] -= 1
                out[j] += 1
    return out


def _apportion(raw: list[float], total: int) -> list[int]:
    out = [math.floor(x) for x in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)
    diff = total - sum(out)
    while diff > 0:
        for i in order:
            if diff == 0:
                break
            out[i] += 1
            diff -= 1
    while diff < 0:
        i = next(i for i in reversed(order) if out[i] > 0)
        out[i] -= 1
        diff += 1
    return out


def block(g: dict) -> dict:
    """Integer means and middle-95% ranges; the means reconcile:
    caught + never_selected + reviewed_not_caught = injected, missed = injected - caught."""
    inj, sel, cau = g["inj"], g["sel"], g["cau"]
    never = [i - s for i, s in zip(inj, sel)]
    rnc = [s - c for s, c in zip(sel, cau)]
    missed = [i - c for i, c in zip(inj, cau)]
    out = dict(injected=summarize(inj), caught=summarize(cau), never_selected=summarize(never),
               reviewed_not_caught=summarize(rnc), missed=summarize(missed),
               share_caught=share_summary(cau, inj))
    settle(out)
    return out


def settle(b: dict) -> None:
    b["missed"][0] = b["injected"][0] - b["caught"][0]
    b["reviewed_not_caught"][0] = max(0, b["missed"][0] - b["never_selected"][0])
    b["never_selected"][0] = b["missed"][0] - b["reviewed_not_caught"][0]


def by_type_blocks(st: dict, level: str, top: dict) -> dict:
    """Per-type blocks whose integer means add up to the displayed totals (largest remainder)."""
    out = {t: block(st[f"{level}:{t}"]) for t in ERROR_TYPES}
    per_run = {
        "injected": lambda g: g["inj"], "caught": lambda g: g["cau"],
        "never_selected": lambda g: [i - s for i, s in zip(g["inj"], g["sel"])],
    }
    for field, get in per_run.items():
        cap = {"injected": None,
               "caught": [out[t]["injected"][0] for t in ERROR_TYPES],
               "never_selected": [out[t]["injected"][0] - out[t]["caught"][0] for t in ERROR_TYPES]}[field]
        ints = apportion([mean(get(st[f"{level}:{t}"])) for t in ERROR_TYPES], top[field][0], cap)
        for t, v in zip(ERROR_TYPES, ints):
            out[t][field][0] = v
    for t in ERROR_TYPES:
        settle(out[t])
    return out


def simulate_strategies(agent: list[dict], rng: random.Random) -> tuple[list[dict], list[dict], list[dict]]:
    n = A["N_OUTPUTS"]
    weights = [t["likelihood"] for t in agent]
    keys = ["inj", "sel", "cau"]
    groups = (["hi", "all"] + [f"hi:{t}" for t in ERROR_TYPES] + [f"all:{t}" for t in ERROR_TYPES]
              + [f"mode:{m}" for m in LOPA_MODES])
    stats = {(m, b, c): {g: {k: [] for k in keys} for g in groups}
             for m in METHODS for b in BUDGETS for c in CATCH_RATES}
    # No-load sweep over review counts (the team staffed for it), for the capacity point and the
    # ceiling. The capacity point is exact: TEAM_CAPACITY reviews a day over the period.
    cap_k = A["TEAM_CAPACITY"] * A["PERIOD_DAYS"]
    sweep_ks = sorted({round(n * b / 100) for b in range(1, 101)} | {cap_k})
    sweep = {(m, c): {k: [] for k in sweep_ks} for m in METHODS for c in CATCH_RATES}
    sweep_inj = []

    for _ in range(N_RUNS):
        n_err = sum(1 for _ in range(n) if rng.random() < A["ERROR_RATE"])
        types = [rng.choices(agent, weights)[0] for _ in range(n_err)]
        top_up_modes(types, agent, rng)
        errors = []
        for t in types:
            et = ERROR_TYPE[t["id"]]
            score = rng.gauss(A["GUARDRAIL_STRENGTH"] * (A["GUARDRAIL_SIGNAL"][et]
                              + A["GUARDRAIL_SEVERITY_SLOPE"] * t["severity"]), 1.0)
            errors.append(dict(type=et, mode=MODE_OF.get(t["id"]), hi=t["rpn"] >= HIGH_IMPACT_RPN,
                               score=score,
                               plausible=rng.random() < A["PLAUSIBLE_BY_DETECTABILITY"][t["detectability"]],
                               u=rng.random()))
        clean_scores = sorted(rng.gauss(0, 1) for _ in range(n - n_err))
        # rank of each error among all outputs (0 = first selected)
        perm = rng.sample(range(n), n)
        err_scores = sorted((e["score"] for e in errors), reverse=True)
        for i, e in enumerate(errors):
            e["rank_random"] = perm[i]
            higher_clean = len(clean_scores) - bisect.bisect_right(clean_scores, e["score"])
            higher_err = sum(1 for s in err_scores if s > e["score"])
            e["rank_risk"] = higher_clean + higher_err
        for e in errors:
            e["groups"] = (["all", f"all:{e['type']}"] + (["hi", f"hi:{e['type']}"] if e["hi"] else [])
                           + ([f"mode:{e['mode']}"] if e["mode"] else []))
        inj = dict.fromkeys(groups, 0)
        for e in errors:
            for g in e["groups"]:
                inj[g] += 1
        sweep_inj.append(inj["hi"])

        for m in METHODS:
            rank = "rank_random" if m == "Random sampling" else "rank_risk"
            by_rank = sorted(errors, key=lambda e: e[rank])
            ranks = [e[rank] for e in by_rank]
            for b in BUDGETS:
                k = round(n * b / 100)
                sel = by_rank[:bisect.bisect_left(ranks, k)]
                caught = review_queue(rng, k, sel, CATCH_RATES, k / A["PERIOD_DAYS"])
                n_sel = dict.fromkeys(groups, 0)
                for e in sel:
                    for g in e["groups"]:
                        n_sel[g] += 1
                for c in CATCH_RATES:
                    st = stats[(m, b, c)]
                    cmap = caught[c]
                    n_cau = dict.fromkeys(groups, 0)
                    for e in sel:
                        if cmap[id(e)]:
                            for g in e["groups"]:
                                n_cau[g] += 1
                    for g in groups:
                        st[g]["inj"].append(inj[g])
                        st[g]["sel"].append(n_sel[g])
                        st[g]["cau"].append(n_cau[g])
            for k in sweep_ks:
                sel = by_rank[:bisect.bisect_left(ranks, k)]
                caught = review_queue(rng, k, sel, CATCH_RATES, 0)  # staffed for it: no load penalty
                for c in CATCH_RATES:
                    sweep[(m, c)][k].append(sum(1 for e in sel if e["hi"] and caught[c][id(e)]))

    cells = []
    for (m, b, c), st in stats.items():
        k = round(n * b / 100)
        minutes = k * A["MIN_PER_REVIEW"]
        hi_b, all_b = block(st["hi"]), block(st["all"])
        per_run = sorted(minutes / x if x else math.inf for x in st["hi"]["cau"])
        lo = per_run[int(0.025 * (len(per_run) - 1))]
        hi = per_run[math.ceil(0.975 * (len(per_run) - 1))]
        caught_int = hi_b["caught"][0]
        hi_types, all_types = by_type_blocks(st, "hi", hi_b), by_type_blocks(st, "all", all_b)
        cells.append(dict(
            method=m, budget=b, catch_rate=c, reviewed=k, reviews_per_day=round(k / A["PERIOD_DAYS"]),
            review_minutes=round(minutes),
            # minutes / the displayed (integer) catches, so the tile's numbers agree arithmetically
            minutes_per_hi_caught=[round(minutes / caught_int) if caught_int else None,
                                   round(lo) if math.isfinite(lo) else None,
                                   round(hi) if math.isfinite(hi) else None],
            hi=hi_b, all=all_b,
            by_type={t: dict(hi=hi_types[t], all=all_types[t]) for t in ERROR_TYPES},
            by_mode={name: block(st[f"mode:{name}"]) for name in LOPA_MODES},
        ))

    # Share of high-impact errors caught at team capacity, and the ceiling: the most any budget
    # catches (reviewing everything included). Integer counts, so percentages derive from them.
    inj_int = round(mean(sweep_inj))
    at_capacity = [dict(method=m, catch_rate=c, budget_pct=round(cap_k * 100 / n, 1),
                        reviews_per_day=round(cap_k / A["PERIOD_DAYS"]), injected=inj_int,
                        caught=round(mean(sweep[(m, c)][cap_k])))
                   for m in METHODS for c in CATCH_RATES]
    ceiling = []
    for c in CATCH_RATES:
        best_m, best_k = max(((m, k) for m in METHODS for k in sweep_ks),
                             key=lambda mk: mean(sweep[(mk[0], c)][mk[1]]))
        ceiling.append(dict(catch_rate=c, injected=inj_int, caught=round(mean(sweep[(best_m, c)][best_k])),
                            method=best_m, budget_pct=round(best_k * 100 / n, 1),
                            reviews_per_day=round(best_k / A["PERIOD_DAYS"])))
    return cells, at_capacity, ceiling


# ------------------------------ snowball ------------------------------
def simulate_snowball(rng: random.Random, risk_select_p: float) -> dict:
    """Stale NAV feed from minute 0. Answers that use it arrive through the day. Random sampling
    fixes each catch as a one-off; risk-based links clustered freshness flags to the feed and
    blocks later answers."""
    day, n_ans = A["SNOWBALL_DAY_MIN"], A["SNOWBALL_ANSWERS"]
    p_catch = A["BASE_CATCH"] * A["PLAUSIBLE_MULT"]  # "-1.6%, no action needed" looks plausible
    step = 15
    grid = list(range(0, day + 1, step))
    series = {m: [[] for _ in grid] for m in METHODS}
    arrived = [[] for _ in grid]
    res = {m: dict(reached=[], selected=[], caught=[], blocked=[], detect=[]) for m in METHODS}
    for _ in range(N_RUNS):
        times = sorted(rng.uniform(0, day) for _ in range(n_ans))
        for gi, t in enumerate(grid):
            arrived[gi].append(bisect.bisect_right(times, t))
        for m in METHODS:
            p_sel = A["DEFAULT_BUDGET"] / 100 if m == "Random sampling" else risk_select_p
            reached_t, sel_n, caught_n, blocked_n = [], 0, 0, 0
            first_catch, root_t, flagged = None, None, 0
            for t in times:
                if root_t is not None and t >= root_t:
                    blocked_n += 1
                    continue
                selected = rng.random() < p_sel
                caught = selected and rng.random() < p_catch
                sel_n += selected
                if caught:
                    caught_n += 1
                    first_catch = t if first_catch is None else first_catch
                else:
                    reached_t.append(t)
                if m == "Risk-based" and selected:
                    flagged += 1
                    if flagged == A["ROOT_CAUSE_CLUSTER"] and root_t is None:
                        root_t = t + A["INVESTIGATION_MIN"]
            r = res[m]
            r["reached"].append(len(reached_t))
            r["selected"].append(sel_n)
            r["caught"].append(caught_n)
            r["blocked"].append(blocked_n)
            detect = first_catch if m == "Random sampling" else root_t
            r["detect"].append(detect if detect is not None else math.inf)
            for gi, t in enumerate(grid):
                series[m][gi].append(bisect.bisect_right(reached_t, t))

    outcome = []
    for m in METHODS:
        r = res[m]
        d = sorted(r["detect"])
        med = d[len(d) // 2]
        found = sum(1 for x in r["detect"] if math.isfinite(x))
        o = dict(method=m, affected=n_ans, households=A["SNOWBALL_HOUSEHOLDS"],
                 reviewed=summarize(r["selected"]), caught=summarize(r["caught"]),
                 blocked=summarize(r["blocked"]), clients_reached=summarize(r["reached"]),
                 detection_min=round(med) if math.isfinite(med) else None,
                 detection_kind="first catch (one-off fix)" if m == "Random sampling" else "root cause identified",
                 detected_share_of_runs=round(found / N_RUNS, 3),
                 root_cause_found=(m == "Risk-based"))
        # reconcile integer means: reached + caught + blocked = affected. Clients reached is kept as
        # simulated (it matches the end of the time series); the smaller term absorbs rounding.
        if m == "Random sampling":
            o["caught"][0] = n_ans - o["clients_reached"][0] - o["blocked"][0]
        else:
            o["blocked"][0] = n_ans - o["clients_reached"][0] - o["caught"][0]
        outcome.append(o)
    ts = []
    for gi, t in enumerate(grid):
        row = dict(t=t, affected=round(sum(arrived[gi]) / N_RUNS))
        for m, key in [("Random sampling", "random"), ("Risk-based", "risk")]:
            row[key] = summarize(series[m][gi])
        ts.append(row)
    nodes = [
        dict(node="nav", label="Vendor NAV feed", stage=0, area="System execution", affected=1, unit="fund", state="root", note="Growth fund NAV not updated from minute 0; prior close reused"),
        dict(node="cache", label="Price cache", stage=1, area="System execution", affected=1, unit="stale price", state="propagating", note="Stale price served with no as-of timestamp"),
        dict(node="hold", label="Holdings join", stage=2, area="System execution", affected=A["SNOWBALL_HOUSEHOLDS"], unit="households hold it", state="propagating", note=f"{A['SNOWBALL_HOUSEHOLDS']} households hold the fund"),
        dict(node="calc", label="Impact calculator", stage=3, area="Reasoning", affected=n_ans, unit="answers that day", state="propagating", note="Fund shown as flat; loss understated"),
        dict(node="answer", label="Agent answer", stage=4, area="Reasoning", affected=n_ans, unit="answers", state="propagating", note="'-1.6%, no action needed' reads as plausible"),
        dict(node="review", label="Human review", stage=5, area=CONTROL_LAYER, affected=n_ans, unit="answers", state="control", note="Control layer: the reviewer checks the number, not its base or freshness"),
        dict(node="client", label="Sent to clients", stage=6, area=CONTROL_LAYER, affected=None, unit="clients reached", state="harm", note="Answers not caught and not blocked"),
    ]
    edges = [dict(source=a, target=b) for a, b in
             [("nav", "cache"), ("cache", "hold"), ("hold", "calc"), ("calc", "answer"), ("answer", "review"), ("review", "client")]]
    return dict(budget_pct=A["DEFAULT_BUDGET"], catch_rate=A["BASE_CATCH"], step_min=step,
                risk_select_share=round(risk_select_p, 4), catch_prob=round(p_catch, 4),
                outcome=outcome, series=ts, nodes=nodes, edges=edges)


# ------------------------------ quality over time ------------------------------
def simulate_quality(rng: random.Random, agent: list[dict]) -> dict:
    """One seeded 14-day realization. Each day's random slice holds CANARY_PER_DAY seeded known
    errors and QUALITY_SLICE - CANARY_PER_DAY real answers, shuffled together. Real errors and
    canaries come from the natural likelihood-weighted mix (no oversampling) and go through the same
    reviewer model, so a reviewer cannot tell them apart. The error rate is corrected for reviewer
    misses with the sensitivity measured on the canaries."""
    weights = [t["likelihood"] for t in agent]
    n_can = A["CANARY_PER_DAY"]
    n_real = A["QUALITY_SLICE"] - n_can
    n_draws = A["CORRECTION_DRAWS"]
    days, above, alert_day, alerts = [], 0, None, []
    seen = caught_cum = 0
    for day in range(1, A["QUALITY_DAYS"] + 1):
        true_rate = A["ERROR_RATE"] + max(0, day - A["DRIFT_START_DAY"] + 1) * A["DRIFT_PER_DAY"]
        items = [(False, rng.random() < true_rate) for _ in range(n_real)] + [(True, True)] * n_can
        rng.shuffle(items)
        found = present = can_caught = streak = 0
        for canary, is_err in items:
            if not is_err:
                streak += 1
                continue
            t = rng.choices(agent, weights)[0]
            plausible = rng.random() < A["PLAUSIBLE_BY_DETECTABILITY"][t["detectability"]]
            hit = rng.random() < catch_prob(A["BASE_CATCH"], streak, A["QUALITY_SLICE"], plausible)
            streak = 0
            if canary:
                can_caught += hit
            else:
                present += 1
                found += hit
        seen += n_can
        caught_cum += can_caught
        raw = found / n_real
        sens = caught_cum / seen
        corrected = min(1.0, raw / sens) if sens else (1.0 if found else 0.0)
        draws = sorted(min(1.0, rng.betavariate(found + 1, n_real - found + 1)
                           / rng.betavariate(caught_cum + 1, seen - caught_cum + 1))
                       for _ in range(n_draws))
        # the interval is widened to include the point estimate when it falls outside (0 found)
        lo = min(corrected, draws[int(0.025 * (n_draws - 1))])
        hi = max(corrected, draws[math.ceil(0.975 * (n_draws - 1))])
        s_lo, s_hi = wilson(caught_cum, seen)
        above = above + 1 if corrected > A["ALERT_LIMIT"] else 0
        if above >= A["ALERT_DAYS"]:
            alerts.append(day)
            alert_day = alert_day or day
        days.append(dict(day=day, reviewed=n_real, errors_found=found, errors_present=present,
                         raw_rate=round(raw, 4), canaries_day=n_can, canaries_caught_day=can_caught,
                         canaries_seen=seen, canaries_caught=caught_cum,
                         sensitivity=round(sens, 4), sensitivity_lo=round(s_lo, 4), sensitivity_hi=round(s_hi, 4),
                         corrected=round(corrected, 4), corrected_lo=round(lo, 4), corrected_hi=round(hi, 4),
                         true_rate=round(true_rate, 4)))
    return dict(days=days, slice=A["QUALITY_SLICE"], canary_per_day=n_can, real_per_day=n_real,
                correction_draws=n_draws, alert_limit=A["ALERT_LIMIT"], alert_days=A["ALERT_DAYS"],
                drift_start_day=A["DRIFT_START_DAY"], alert_day=alert_day, alerts=alerts,
                drift_alert_day=next((d for d in alerts if d >= A["DRIFT_START_DAY"]), None),
                illustrative=True)


# ------------------------------ reviewer experiment ------------------------------
def simulate_oversight(rng: random.Random) -> list[dict]:
    base = A["BASE_CATCH"]
    conditions = [
        ("C1", "H1", "Baseline: answer text only", base),
        ("C2", "H2", "Coverage and freshness banner", min(0.98, base * A["BANNER_MULT"])),
        ("C3", "H3", "After 50 clean items", catch_prob(base, A["FATIGUE_FULL"], 0, False)),
        ("C4", "H4", "30 items queued", catch_prob(base, 0, 2 * A["TEAM_CAPACITY"], False)),
        ("C5", "H5", "Wrong number looks plausible", catch_prob(base, 0, 0, True)),
        ("C6", "H7", "Alert fatigue (alerts on 40% of items)", base * A["ALERT_FATIGUE_MULT"]),
    ]
    n = A["EXPERIMENT_ITEMS"]
    return [dict(condition=c, linked=h, label=label, reviewers=A["EXPERIMENT_REVIEWERS"], shown=n,
                 caught=sum(1 for _ in range(n) if rng.random() < p), model_p=round(p, 3),
                 illustrative=True)
            for c, h, label, p in conditions]


def main(xlsx: Path, out: Path) -> None:
    rng = random.Random(SEED)
    out.mkdir(parents=True, exist_ok=True)
    ws = openpyxl.load_workbook(xlsx)["Test Cases"]
    rows = build_register(ws, rng)
    agent = [r for r in rows if r["is_agent"]]

    log.info("simulating %d runs of the inspection strategies", N_RUNS)
    cells, at_capacity, ceiling = simulate_strategies(agent, rng)
    default = next(c for c in cells if c["method"] == "Risk-based" and c["budget"] == A["DEFAULT_BUDGET"]
                   and c["catch_rate"] == A["BASE_CATCH"])
    sysx = default["by_type"]["System execution"]["all"]
    risk_select_p = 1 - sysx["never_selected"][0] / sysx["injected"][0]

    log.info("simulating the snowball, quality series and reviewer experiment")
    snowball = simulate_snowball(rng, risk_select_p)
    quality = simulate_quality(rng, agent)
    oversight = simulate_oversight(rng)

    meta = dict(
        seed=SEED, n_runs=N_RUNS, high_impact_rpn=HIGH_IMPACT_RPN,
        high_impact_types=[r["id"] for r in agent if r["high_impact"]],
        error_types=ERROR_TYPES, control_layer=CONTROL_LAYER, methods=METHODS,
        budgets=BUDGETS, catch_rates=CATCH_RATES,
        default_budget=A["DEFAULT_BUDGET"], default_catch_rate=A["BASE_CATCH"],
        team_capacity=A["TEAM_CAPACITY"], outputs=A["N_OUTPUTS"], period_days=A["PERIOD_DAYS"],
        outputs_per_day=A["N_OUTPUTS"] // A["PERIOD_DAYS"], min_per_review=A["MIN_PER_REVIEW"],
        at_capacity=at_capacity, ceiling=ceiling,
        lopa_modes=LOPA_MODES, min_injected_per_mode=A["MIN_INJECTED_PER_MODE"],
        assumptions=[dict(name=nm, value=v, unit=u, comment=cm) for nm, v, u, cm in ASSUMPTIONS],
    )
    files = {"risks": rows, "sim_grid": cells, "sim_meta": meta, "snowball": snowball,
             "quality": quality, "oversight": oversight}
    for name, data in files.items():
        indent = None if name == "sim_grid" else 1
        (out / f"{name}.json").write_text(json.dumps(data, indent=indent, separators=(",", ":") if indent is None else None))
    for old in ["strategy_curve", "strategy_summary", "strategy_by_area",
                "snowball_nodes", "snowball_edges", "snowball_outcome"]:
        (out / f"{old}.json").unlink(missing_ok=True)
    log.info("wrote %s", out)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main(Path(sys.argv[1]), Path(sys.argv[2]))
