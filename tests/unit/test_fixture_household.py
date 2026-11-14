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
    for h in trace.holdings:
        assert h.price == inst.loc[h.instrument_id, "reference_price"]
        assert h.instrument_type == inst.loc[h.instrument_id, "instrument_type"]
    assert sum(h.qty * h.price for h in trace.holdings) == trace.base == 1_200_000
    want = ["taxable", "ira", "401k_held_away"]
    assert sorted(set(acct_type.values())) == sorted(want) == sorted(trace.accounts_in_crm)
