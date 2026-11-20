import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from advisor_qa.checks.rules import CHECKS, CrmDirectory, run_checks
from advisor_qa.eval.evaluate import check_diagnostics, evaluate
from advisor_qa.inject.modes import MODES
from advisor_qa.pipeline.questions import Question
from advisor_qa.schema.trace import NumberLink, RunTrace, ToolCall
from advisor_qa.scoring.weighted import FlagCount, WeightedSum
from advisor_qa.world import fixture as fx
from tests.pipeline_world import Kit

CLEAN_RUN = Path(__file__).parent.parent / "fixtures" / "clean_run.json"
SUPPORT = {name for name, (_, mode) in CHECKS.items() if mode is None}


@pytest.fixture
def clean() -> RunTrace:
    return RunTrace.model_validate(json.loads(CLEAN_RUN.read_text()))


@pytest.fixture(scope="module")
def directory() -> CrmDirectory:
    return CrmDirectory({fx.FIXTURE_HOUSEHOLD_ID: fx.FIXTURE_ADVISOR_ID}, {fx.FIXTURE_HOUSEHOLD_ID:
                        "Avery Chen"})  # fmt: skip


def fired(t: RunTrace, d: CrmDirectory) -> set[str]:
    return {k for k, v in run_checks(t, d).items() if v}


def test_clean_run_fires_nothing(clean: RunTrace, directory: CrmDirectory) -> None:
    assert fired(clean, directory) == set()


def _broken(clean: RunTrace, how: str) -> RunTrace:
    t = clean.model_copy(deep=True)
    if how == "stale_fund_nav":
        h = next(h for h in t.holdings if h.instrument_type == "mutual_fund")
        h.price_as_of, h.price_source = datetime(2026, 3, 9, 16), "nav"
    elif how == "account_coverage":
        t.accounts_in_crm = ["ira", "taxable"]
    elif how == "tool_failure":
        t.tool_calls[1] = t.tool_calls[1].model_copy(update={"status": "timeout"})
    elif how == "price_source":
        t.holdings[0].price_source = "vendor_b"
    elif how == "base_reconciliation":
        t.base = t.base * 1.05
    elif how == "percent_scale":
        t.number_to_tool_output_links[1] = NumberLink(number="-0.02%", source=None)
    elif how == "stale_snapshot":
        t.data_snapshot_id = "snap-2026-03-05-1600"
    elif how == "unexpected_tool":
        t.tool_calls.append(ToolCall(tool_name="send_email", args={}, status="ok",
                                     latency_ms=1.0, output_hash="0" * 12))  # fmt: skip
    elif how == "advice":
        t.certainty_and_advice_phrases = ["i'd recommend"]
    elif how == "unlabelled_estimate":
        t.estimate_label = False
    elif how == "unassigned_access":
        t.advisor_id = "ADV-0001"
    elif how == "implausible_move":
        t.holdings[0].price = t.holdings[0].price / 2
    elif how == "mixed_timestamps":
        t.holdings[0].price_as_of = datetime(2026, 3, 10, 12, 0)
    return t


@pytest.mark.parametrize(
    "check",
    ["stale_fund_nav", "account_coverage", "tool_failure", "price_source", "base_reconciliation",
     "percent_scale", "stale_snapshot", "unexpected_tool", "advice", "unlabelled_estimate",
     "unassigned_access", "implausible_move", "mixed_timestamps"],
)  # fmt: skip
def test_hand_broken_trace_fires_its_check(
    clean: RunTrace, directory: CrmDirectory, check: str
) -> None:
    assert check in fired(_broken(clean, check), directory)


@pytest.fixture(scope="module")
def kit() -> Kit:
    return Kit()


@pytest.fixture(scope="module")
def questions(kit: Kit) -> list[Question]:
    out: list[Question] = []
    for d in kit.world.prices.calendar.days[:25]:
        out += kit.questions.day(d, kit.households(), fixture_day=False)
    return out


def _crm(kit: Kit) -> CrmDirectory:
    return CrmDirectory(kit.directory.advisor_of, kit.directory.name_of)


def test_generated_clean_runs_fire_only_support_signals(
    kit: Kit, questions: list[Question]
) -> None:
    d = _crm(kit)
    for q in questions:
        got = fired(kit.agent.run(q).trace, d) - {"ambiguous_surname"}
        assert got <= set(), (q.kind, q.prompt, got)


MAPPED = {mode: name for name, (_, mode) in CHECKS.items() if mode}


@pytest.mark.parametrize("mode", [m.id for m in MODES])
def test_each_injected_mode_is_caught_by_its_check(
    kit: Kit, questions: list[Question], mode: str
) -> None:
    incident = "D5-test" if mode == "D5" else None
    chosen = ["D1", "O1"] if mode == "O1" else [mode]
    d = _crm(kit)
    caught = total = 0
    for q in questions:
        if mode not in kit.injector.exposure(q, incident):
            continue
        if mode == "O1" and "D1" not in kit.injector.exposure(q):
            continue
        p = kit.injector.build(q, chosen, incident)
        total += 1
        caught += MAPPED[mode] in fired(kit.agent.run(p.question, p.faults).trace, d)
        if total == 20:
            break
    assert total and caught / total >= 0.5, (mode, caught, total)


def test_weighted_sum_and_count() -> None:
    w = WeightedSum.from_catalogue()
    assert set(w.weights) == set(CHECKS)
    assert w.weights["stale_fund_nav"] == 4 and w.weights["llm_calculation"] == 1
    flags = {"stale_fund_nav": True, "advice": True, "price_source": False}
    assert w.score(flags) == 4 + 5 and FlagCount().score(flags) == 2
    with pytest.raises(ValueError):
        WeightedSum({"no_such_check": 1.0})


def test_evaluate_risk_beats_random_on_a_toy_split() -> None:
    n = 200
    ids = [f"r{i:03d}" for i in range(n)]
    err = [i % 10 == 0 for i in range(n)]  # 10% prevalence
    flags = pd.DataFrame({c: [False] * n for c in CHECKS}, index=ids)
    flags.loc[[r for r, e in zip(ids, err, strict=True) if e], "stale_fund_nav"] = True
    lab = pd.DataFrame(
        {"error": err, "modes": [["D1"] if e else [] for e in err],
         "severity": [4.0 if e else 0.0 for e in err]},
        index=ids,
    )  # fmt: skip
    rep = evaluate(flags, lab, [0.05, 0.10], 1, WeightedSum.from_catalogue())
    five = rep["capacities"]["0.05"]
    assert five["k"] == 10 and five["risk"]["errors_caught"] == 10
    assert five["random_mean"]["errors_caught"] == pytest.approx(1.0, abs=0.3)
    assert rep["capacities"]["0.1"]["risk"]["recall"] == 1.0
    diag = check_diagnostics(flags, lab).set_index("check")
    assert (
        diag.loc["stale_fund_nav", "precision"] == 1 and diag.loc["stale_fund_nav", "recall"] == 1
    )


def test_support_checks_are_unmapped() -> None:
    assert SUPPORT == {"ambiguous_surname", "llm_calculation", "missing_expected_tool",
                       "missing_disclosure"}  # fmt: skip
    assert sorted(MAPPED) == sorted(m.id for m in MODES)
