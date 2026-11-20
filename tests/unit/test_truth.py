from datetime import date

import pytest

from advisor_qa.truth import Truth, window_start
from advisor_qa.world import fixture as fx
from advisor_qa.world.households import fixture_tables
from advisor_qa.world.instruments import load_instruments
from advisor_qa.world.price_params import load_price_config
from advisor_qa.world.prices import PriceWorld, build_prices

HH = fx.FIXTURE_HOUSEHOLD_ID


@pytest.fixture(scope="module")
def world() -> PriceWorld:
    seed, p = load_price_config()
    return build_prices(load_instruments(), p, seed)


@pytest.fixture(scope="module")
def truth(world: PriceWorld) -> Truth:
    t = fixture_tables(load_instruments())
    return Truth(t.holdings, t.accounts, world)


def test_window_start(world: PriceWorld) -> None:
    days = world.calendar.days
    assert window_start("intraday", date(2026, 3, 10), days) is None
    assert window_start("wtd", date(2026, 3, 12), days) == date(2026, 3, 9)
    assert window_start("mtd", date(2026, 3, 12), days) == date(2026, 3, 2)
    assert window_start("ytd", date(2026, 3, 12), days) == date(2026, 1, 2)
    with pytest.raises(ValueError):
        window_start("qtd", date(2026, 3, 12), days)


def test_since_today_equals_intraday(truth: Truth) -> None:
    scope = truth.all_accounts(HH)
    a = truth.impact(HH, "2026-03-10T14:05:00", scope)
    b = truth.impact(HH, "2026-03-10T14:05:00", scope, since="2026-03-10")
    assert a == b


def test_multi_day_window_chains_daily_moves(truth: Truth, world: PriceWorld) -> None:
    scope = truth.all_accounts(HH)
    close_11 = truth.impact(HH, "2026-03-11T16:00:00", scope, since="2026-03-10")
    day_10 = truth.impact(HH, "2026-03-10T16:00:00", scope)
    day_11 = truth.impact(HH, "2026-03-11T16:00:00", scope)
    assert close_11.dollars == pytest.approx(day_10.dollars + day_11.dollars, abs=0.02)
    assert close_11.base == day_10.base


def test_empty_scope_and_bad_inputs(truth: Truth) -> None:
    z = truth.impact(HH, "2026-03-10T14:05:00", [])
    assert z.dollars == 0 and z.pct == 0 and z.base == 0
    with pytest.raises(ValueError):
        truth.impact(HH, "2026-03-10T14:05:00", ["ACC-99999"])
    with pytest.raises(ValueError):
        truth.impact(HH, "2026-03-10T14:05:00", [], since="2026-03-11")


def test_per_holding_sums_to_total(truth: Truth) -> None:
    got = truth.impact(HH, "2026-03-10T14:05:00", truth.all_accounts(HH))
    assert sum(got.by_holding.values()) == pytest.approx(got.dollars, abs=0.05)
    assert got.by_holding[("ACC-88122", "INS-03")] == pytest.approx(-2_100, abs=0.01)
