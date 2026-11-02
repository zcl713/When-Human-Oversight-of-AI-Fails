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
]
A = {name: value for name, value, _, _ in ASSUMPTIONS}

BUDGETS = list(range(2, 31))  # % of answers reviewed
CATCH_RATES = [0.40, 0.50, 0.62, 0.75, 0.90]
METHODS = ["Random sampling", "Risk-based"]
ERROR_TYPES = ["Reasoning", "Planning", "System execution"]
CONTROL_LAYER = "Control layer (human review)"
ILLUSTRATIVE_EVIDENCE = "Illustrative sample result; not yet run"

ERROR_TYPE = {}
for k in "D1 D2 D3 D4 D5 D6 D7 C5 S2 S4".split():
    ERROR_TYPE[k] = "System execution"
for k in "C4 C6 I1 I2 I4 I5 I6 O5 S1".split():
    ERROR_TYPE[k] = "Planning"
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
def build_register(ws, rng: random.Random) -> list[dict]:
    rows = []
    for r in ws.iter_rows(min_row=5, values_only=True):
        if not r[0]:
            continue
        rid, layer, cat, test, setup, prompt, exp, passc, ctrl, s, l, d = r[:12]
        reg = r[14]
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
        ))
    return rows


# ------------------------------ inspection Monte Carlo ------------------------------
def simulate_strategies(agent: list[dict], rng: random.Random) -> tuple[list[dict], dict]:
    n = A["N_OUTPUTS"]
    weights = [t["likelihood"] for t in agent]
    # stats[(method, budget, rate)] -> dict of per-run lists
    keys = ["inj", "sel", "cau"]
    groups = ["hi", "all"] + [f"hi:{t}" for t in ERROR_TYPES] + [f"all:{t}" for t in ERROR_TYPES]
    stats = {(m, b, c): {g: {k: [] for k in keys} for g in groups}
             for m in METHODS for b in BUDGETS for c in CATCH_RATES}
    # no-load sweep for "reviews needed to catch 50% of high-impact errors"
    sweep = {(m, c): {b: [] for b in range(1, 101)} for m in METHODS for c in CATCH_RATES}
    sweep_inj = []

    for _ in range(N_RUNS):
        n_err = sum(1 for _ in range(n) if rng.random() < A["ERROR_RATE"])
        errors = []
        for _ in range(n_err):
            t = rng.choices(agent, weights)[0]
            et = ERROR_TYPE[t["id"]]
            score = rng.gauss(A["GUARDRAIL_STRENGTH"] * (A["GUARDRAIL_SIGNAL"][et]
                              + A["GUARDRAIL_SEVERITY_SLOPE"] * t["severity"]), 1.0)
            errors.append(dict(type=et, hi=t["rpn"] >= HIGH_IMPACT_RPN, score=score,
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
            e["groups"] = ["all", f"all:{e['type']}"] + (["hi", f"hi:{e['type']}"] if e["hi"] else [])
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
            for b in range(1, 101):
                k = round(n * b / 100)
                sel = by_rank[:bisect.bisect_left(ranks, k)]
                daily_no_load = 0  # staffed for it: no load penalty
                caught = review_queue(rng, k, sel, CATCH_RATES, daily_no_load)
                for c in CATCH_RATES:
                    sweep[(m, c)][b].append(sum(1 for e in sel if e["hi"] and caught[c][id(e)]))

    def block(g: dict) -> dict:
        inj, sel, cau = g["inj"], g["sel"], g["cau"]
        never = [i - s for i, s in zip(inj, sel)]
        rnc = [s - c for s, c in zip(sel, cau)]
        missed = [i - c for i, c in zip(inj, cau)]
        out = dict(injected=summarize(inj), caught=summarize(cau), never_selected=summarize(never),
                   reviewed_not_caught=summarize(rnc), missed=summarize(missed),
                   share_caught=share_summary(cau, inj))
        # make the integer means reconcile: caught + never_selected + reviewed_not_caught = injected
        out["missed"][0] = out["injected"][0] - out["caught"][0]
        out["reviewed_not_caught"][0] = out["missed"][0] - out["never_selected"][0]
        return out

    cells = []
    for (m, b, c), st in stats.items():
        k = round(n * b / 100)
        minutes = k * A["MIN_PER_REVIEW"]
        hi_caught = st["hi"]["cau"]
        per_run = sorted(minutes / x if x else math.inf for x in hi_caught)
        lo = per_run[int(0.025 * (len(per_run) - 1))]
        hi = per_run[math.ceil(0.975 * (len(per_run) - 1))]
        mean_caught = sum(hi_caught) / len(hi_caught)
        cells.append(dict(
            method=m, budget=b, catch_rate=c, reviewed=k, reviews_per_day=round(k / A["PERIOD_DAYS"]),
            review_minutes=round(minutes),
            minutes_per_hi_caught=[round(minutes / mean_caught) if mean_caught else None,
                                   round(lo) if math.isfinite(lo) else None,
                                   round(hi) if math.isfinite(hi) else None],
            hi=block(st["hi"]), all=block(st["all"]),
            by_type={t: dict(hi=block(st[f"hi:{t}"]), all=block(st[f"all:{t}"])) for t in ERROR_TYPES},
        ))

    # Reviews a day needed to catch half the high-impact errors, if the team were staffed for it
    # (no load penalty). None when even reviewing every answer does not get there.
    needed = []
    mean_inj = sum(sweep_inj) / len(sweep_inj)
    for (m, c), sw in sweep.items():
        hit = next((b for b in range(1, 101) if sum(sw[b]) / len(sw[b]) >= 0.5 * mean_inj), None)
        best = max(sum(v) / len(v) / mean_inj for v in sw.values())
        needed.append(dict(
            method=m, catch_rate=c, budget_pct=hit,
            reviews_per_day=round(n * hit / 100 / A["PERIOD_DAYS"]) if hit else None,
            reachable=hit is not None,
            best_share=round(best, 4),
        ))
    return cells, needed


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
        # reconcile integer means: reached + caught + blocked = affected
        o["clients_reached"][0] = n_ans - o["caught"][0] - o["blocked"][0]
        outcome.append(o)
    ts = []
    for gi, t in enumerate(grid):
        row = dict(t=t, affected=round(sum(arrived[gi]) / N_RUNS))
        for m, key in [("Random sampling", "random"), ("Risk-based", "risk")]:
            row[key] = summarize(series[m][gi])
        ts.append(row)
    # final point reconciles with the outcome table
    ts[-1]["random"][0] = outcome[0]["clients_reached"][0]
    ts[-1]["risk"][0] = outcome[1]["clients_reached"][0]
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
def simulate_quality(rng: random.Random) -> dict:
    days, above, alert_day = [], 0, None
    for day in range(1, A["QUALITY_DAYS"] + 1):
        true_rate = A["ERROR_RATE"] + max(0, day - A["DRIFT_START_DAY"] + 1) * A["DRIFT_PER_DAY"]
        n = A["QUALITY_SLICE"]
        is_err = [rng.random() < true_rate for _ in range(n)]
        found, streak = 0, 0
        for e in is_err:
            if e:
                plausible = rng.random() < A["PLAUSIBLE_BY_DETECTABILITY"][4]
                if rng.random() < catch_prob(A["BASE_CATCH"], streak, n, plausible):
                    found += 1
                streak = 0
            else:
                streak += 1
        lo, hi = wilson(found, n)
        est = found / n
        above = above + 1 if est > A["ALERT_LIMIT"] else 0
        if above >= A["ALERT_DAYS"] and alert_day is None:
            alert_day = day
        days.append(dict(day=day, reviewed=n, errors_found=found, errors_present=sum(is_err),
                         estimate=round(est, 4), ci_lo=round(lo, 4), ci_hi=round(hi, 4),
                         true_rate=round(true_rate, 4)))
    return dict(days=days, alert_limit=A["ALERT_LIMIT"], alert_days=A["ALERT_DAYS"],
                drift_start_day=A["DRIFT_START_DAY"], alert_day=alert_day, illustrative=True)


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
    cells, needed = simulate_strategies(agent, rng)
    default = next(c for c in cells if c["method"] == "Risk-based" and c["budget"] == A["DEFAULT_BUDGET"]
                   and c["catch_rate"] == A["BASE_CATCH"])
    sysx = default["by_type"]["System execution"]["all"]
    risk_select_p = 1 - sysx["never_selected"][0] / sysx["injected"][0]

    log.info("simulating the snowball, quality series and reviewer experiment")
    snowball = simulate_snowball(rng, risk_select_p)
    quality = simulate_quality(rng)
    oversight = simulate_oversight(rng)

    meta = dict(
        seed=SEED, n_runs=N_RUNS, high_impact_rpn=HIGH_IMPACT_RPN,
        high_impact_types=[r["id"] for r in agent if r["high_impact"]],
        error_types=ERROR_TYPES, control_layer=CONTROL_LAYER, methods=METHODS,
        budgets=BUDGETS, catch_rates=CATCH_RATES,
        default_budget=A["DEFAULT_BUDGET"], default_catch_rate=A["BASE_CATCH"],
        team_capacity=A["TEAM_CAPACITY"], outputs=A["N_OUTPUTS"], period_days=A["PERIOD_DAYS"],
        outputs_per_day=A["N_OUTPUTS"] // A["PERIOD_DAYS"], min_per_review=A["MIN_PER_REVIEW"],
        reviews_needed_50=needed,
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
