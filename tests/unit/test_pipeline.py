import json
from datetime import date
from pathlib import Path

import pytest

from advisor_qa import derive
from advisor_qa.inject.modes import MODES
from advisor_qa.inject.plan import dollar_error, make_label
from advisor_qa.pipeline.questions import Question
from advisor_qa.schema.trace import RunTrace
from advisor_qa.store.runs import canonical_json, output_hash
from advisor_qa.world import fixture as fx
from tests.pipeline_world import Kit

CLEAN_RUN = Path(__file__).parent.parent / "fixtures" / "clean_run.json"
FIXTURE_DAY = date(2026, 3, 10)


@pytest.fixture(scope="module")
def kit() -> Kit:
    return Kit()


def fixture_trace(kit: Kit) -> RunTrace:
    return kit.agent.run(kit.questions.fixture(FIXTURE_DAY)).trace


def test_fixture_run_is_the_golden_clean_run(kit: Kit) -> None:
    """tests/fixtures/clean_run.json is the generator's own fixture run. To refresh it after an
    intended change: `uv run python -m tests.unit.test_pipeline`."""
    want = RunTrace.model_validate(json.loads(CLEAN_RUN.read_text()))
    assert fixture_trace(kit) == want


def test_fixture_run_numbers(kit: Kit) -> None:
    r = kit.agent.run(kit.questions.fixture(FIXTURE_DAY))
    assert r.claimed_dollars == -21_700
    assert "-$21,700 (-1.8%)" in r.answer
    assert all(link.source for link in r.trace.number_to_tool_output_links)
    calc = next(c for c in r.trace.tool_calls if c.tool_name == "calculate_portfolio_impact")
    assert {link.source for link in r.trace.number_to_tool_output_links} == {
        f"{calc.output_hash}:dollar_impact",
        f"{calc.output_hash}:pct_impact",
    }
    assert r.trace.base == 1_200_000 and r.trace.estimate_label and r.trace.disclosures_present


def _questions(kit: Kit, days: int = 25) -> list[Question]:
    cal = kit.world.prices.calendar
    out: list[Question] = []
    for d in cal.days[:days]:
        out += kit.questions.day(d, kit.households(), fixture_day=False)
    return out


@pytest.fixture(scope="module")
def questions(kit: Kit) -> list[Question]:
    return _questions(kit)


def test_clean_runs_match_truth(kit: Kit, questions: list[Question]) -> None:
    for q in questions:
        r = kit.agent.run(q)
        assert dollar_error(q, r, kit.truth) == 0, (q.prompt, r.answer)
        assert r.trace.interpretation_restated and r.trace.disclosures_present
        assert {t.tool_name for t in r.trace.tool_calls} == set(r.trace.expected_tool_set)
        assert all(c.status == "ok" for c in r.trace.tool_calls)


def test_question_mix_covers_every_kind(questions: list[Question]) -> None:
    assert {q.kind for q in questions} == {
        "impact_all", "impact_scoped", "period", "benchmark", "ambiguous_impact",
        "ambiguous_window", "worried", "sell", "tax", "followup",
    }  # fmt: skip


def _comparable(t: RunTrace) -> dict[str, object]:
    d = t.model_dump(mode="json")
    for c in d["tool_calls"]:
        c.pop("latency_ms")
    return d


@pytest.mark.parametrize("mode", [m.id for m in MODES])
def test_every_mode_changes_the_run_and_is_labelled(
    kit: Kit, questions: list[Question], mode: str
) -> None:
    incident = "D5-test" if mode == "D5" else None
    chosen = ["D1", "O1"] if mode == "O1" else [mode]
    q = next(
        (q for q in questions if mode in kit.injector.exposure(q, incident)
         and (mode != "O1" or "D1" in kit.injector.exposure(q))),
        None,
    )  # fmt: skip
    assert q is not None, f"no exposed question for {mode}"
    planned = kit.injector.build(q, chosen, incident)
    assert planned.modes == chosen
    faulty = kit.agent.run(planned.question, planned.faults)
    base_modes = ["D1"] if mode == "O1" else []
    base = kit.injector.build(q, base_modes, incident)
    clean = kit.agent.run(base.question, base.faults)
    assert _comparable(faulty.trace) != _comparable(clean.trace) or faulty.answer != clean.answer
    label = make_label(planned, faulty, kit.truth, kit.injector.cat)
    assert label.error_present and [m.value for m in label.error_type] == chosen
    assert label.severity >= kit.injector.cat.loc[mode, "severity"]
    if mode == "D5":
        assert label.root_cause_id == "D5-test"


def test_s2_rewrites_to_an_unassigned_household(kit: Kit, questions: list[Question]) -> None:
    q = next(q for q in questions if q.turn == 1)
    p = kit.injector.build(q, ["S2"])
    assert kit.directory.advisor_of[p.question.household_id] != q.advisor_id
    r = kit.agent.run(p.question, p.faults)
    assert r.trace.household_id == p.question.household_id and r.trace.advisor_id == q.advisor_id


def test_exclusive_modes(kit: Kit, questions: list[Question]) -> None:
    q = next(q for q in questions if q.kind == "worried")
    assert kit.injector.build(q, ["D1", "I3", "O3", "S4"]).modes == ["I3", "S4"]
    assert kit.injector.build(q, ["O1"]).modes == []  # O1 needs broken data


def test_injection_rates_and_pinned_fixture(kit: Kit, questions: list[Question]) -> None:
    assert kit.injector.plan(kit.questions.fixture(FIXTURE_DAY)).modes == []
    hit = sum(bool(kit.injector.plan(q).modes) for q in questions)
    assert 0.2 < hit / len(questions) < 0.8
    scaled = kit.injector.__class__(
        kit.world, kit.directory, kit.inj.model_copy(update={"rate_scale": 0.0}), kit.seed
    )
    assert all(scaled.plan(q).modes in ([], ["D5"]) for q in questions)


def test_output_hash_is_canonical() -> None:
    a, b = {"b": 1, "a": [1.5, "x"]}, {"a": [1.5, "x"], "b": 1}
    assert output_hash(a) == output_hash(b) and len(output_hash(a)) == 12
    assert canonical_json(a) == '{"a":[1.5,"x"],"b":1}'


def test_derive_parsing() -> None:
    text = "Down -$21,700 (-1.8%) today; SPY +0.25%. Fund X has returned 11.2% a year."
    assert [v for _, v, _ in derive.parse_numbers(text)] == [-21_700, -1.8, 0.25, 11.2]
    links = derive.link_numbers(text, {"abc": {"dollar_impact": -21_700.4, "pct_impact": -1.8083}})
    assert [link.source for link in links] == ["abc:dollar_impact", "abc:pct_impact", None, None]
    avail = ["taxable", "ira", "401k_held_away"]
    assert derive.requested_accounts("How is the IRA doing today?", avail) == ["ira"]
    assert derive.requested_accounts("Down today, excluding the 401(k)?", avail) == [
        "taxable", "ira",
    ]  # fmt: skip
    assert derive.requested_accounts("How is the Mirabel household?", avail) == avail
    assert derive.requested_window("How has it done this month?") == "mtd"
    assert derive.requested_benchmark("versus the S&P 500 today") == "SPY"
    assert derive.named_surname("How is the Garcia household doing?") == "Garcia"
    assert derive.fmt_pct(-1.8083) == "-1.8%" and derive.fmt_pct(0.256) == "+0.26%"
    assert derive.fmt_pct(-0.0042) == "0.00%" and derive.fmt_dollars(-21_700.4) == "-$21,700"


def test_fixture_question_identity(kit: Kit) -> None:
    q = kit.questions.fixture(FIXTURE_DAY)
    assert (q.household_id, q.advisor_id, q.prompt) == (
        fx.FIXTURE_HOUSEHOLD_ID, fx.FIXTURE_ADVISOR_ID, "How much has my portfolio lost today?",
    )  # fmt: skip
    assert q.pinned_clean and q.turn == 1 and q.window == "intraday"


if __name__ == "__main__":
    t = fixture_trace(Kit())
    CLEAN_RUN.write_text(json.dumps(t.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {CLEAN_RUN}")
