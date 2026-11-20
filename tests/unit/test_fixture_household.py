import json
from pathlib import Path

import pytest

from advisor_qa.schema.trace import RunTrace
from advisor_qa.world import fixture as fx
from advisor_qa.world.checks import check_holdings_sum, check_integrity
from advisor_qa.world.households import fixture_tables
from advisor_qa.world.instruments import load_instruments

# Day moves from the hand-off, by instrument. Price data, so they live in the test, not the tables.
DAY_MOVES = {
    "INS-01": -0.021,  # ETF
    "INS-05": -0.015,  # mutual fund
    "INS-08": -0.010,  # bond fund (duration case)
    "INS-02": -0.021,  # ETF
    "INS-06": -0.020,  # mutual fund
    "INS-03": -0.021,  # ETF, held away
}
CLEAN_RUN = Path(__file__).parent.parent / "fixtures" / "clean_run.json"


def test_fixture_values_and_true_impact() -> None:
    t = fixture_tables(load_instruments())

    assert t.households["total_value"].iloc[0] == 1_200_000
    assert sorted(t.holdings["value_usd"]) == [100_000, 100_000, 150_000, 200_000, 200_000, 450_000]
    impact = sum(r.value_usd * DAY_MOVES[r.instrument_id] for r in t.holdings.itertuples())
    assert impact == pytest.approx(-21_700)
    assert impact / 1_200_000 == pytest.approx(-0.018, abs=0.0001)


def test_fixture_accounts_and_instrument_flags() -> None:
    inst = load_instruments().set_index("instrument_id")
    t = fixture_tables(load_instruments())
    held_away = t.accounts[t.accounts["held_away"]]

    assert list(held_away["account_id"]) == ["ACC-88122"]
    assert set(t.accounts["account_id"]) == set(fx.FIXTURE_ACCOUNT_IDS)
    assert t.households["advisor_id"].iloc[0] == "ADV-0382"
    assert t.holdings[t.holdings["account_id"] == "ACC-88122"]["value_usd"].iloc[0] == 100_000
    mutual_funds = {i for i in DAY_MOVES if inst.loc[i, "is_mutual_fund"]}
    assert {DAY_MOVES[i] for i in mutual_funds} == {
        -0.015,
        -0.020,
    }  # only these two are mutual funds
    assert inst.loc["INS-08", "instrument_type"] == "bond_fund"
    assert check_integrity(t) == [] and check_holdings_sum(t) == []


def test_clean_run_json_matches_fixture() -> None:
    inst = load_instruments().set_index("instrument_id")
    trace = RunTrace.model_validate(json.loads(CLEAN_RUN.read_text()))
    acct_type = dict(fx.FIXTURE_ACCOUNTS)

    assert (
        trace.household_id == fx.FIXTURE_HOUSEHOLD_ID and trace.advisor_id == fx.FIXTURE_ADVISOR_ID
    )
    assert [(h.account_id, h.instrument_id, h.qty) for h in trace.holdings] == fx.FIXTURE_HOLDINGS
    # Holding.price is the price used at the run time; the base is the value at the prior close,
    # which is the reference price on the fixture day (the anchor date is the day before).
    start = trace.inputs["start_prices"]
    for h in trace.holdings:
        assert start[h.instrument_id] == inst.loc[h.instrument_id, "reference_price"]
        assert h.price == pytest.approx(start[h.instrument_id] * (1 + DAY_MOVES[h.instrument_id]))
        assert h.instrument_type == inst.loc[h.instrument_id, "instrument_type"]
    assert sum(h.qty * start[h.instrument_id] for h in trace.holdings) == trace.base == 1_200_000
    assert trace.numbers_claimed == [-21_700, -1.8]
    want = ["taxable", "ira", "401k_held_away"]
    assert sorted(set(acct_type.values())) == sorted(want) == sorted(trace.accounts_in_crm)


def test_truth_function_reproduces_fixture_from_price_layer() -> None:
    """-$21,700 and -1.8% from the real (synthetic) price layer via the truth function, not from
    DAY_MOVES."""
    from advisor_qa.truth import Truth
    from advisor_qa.world.price_params import load_price_config
    from advisor_qa.world.prices import build_prices

    seed, p = load_price_config()
    t = fixture_tables(load_instruments())
    truth = Truth(t.holdings, t.accounts, build_prices(load_instruments(), p, seed))
    hh = fx.FIXTURE_HOUSEHOLD_ID

    got = truth.impact(hh, "2026-03-10T14:05:00", truth.all_accounts(hh))
    assert got.dollars == pytest.approx(-21_700, abs=0.01)
    assert got.base == pytest.approx(1_200_000, abs=0.01)
    assert round(got.pct, 1) == -1.8
    # scope is explicit: dropping the held-away 401(k) drops its -$2,100
    no_401k = truth.impact(hh, "2026-03-10T14:05:00", ["ACC-88120", "ACC-88121"])
    assert no_401k.dollars == pytest.approx(-19_600, abs=0.01)
    assert no_401k.pct == pytest.approx(-19_600 / 1_100_000 * 100, abs=1e-6)
