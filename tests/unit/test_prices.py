"""Acceptance tests for the synthetic price layer (docs/price_simulator_spec.md, section 6).

All prices are synthetic. FRED-mode tests use the made-up yield files in tests/fixtures/fred/,
never the real downloads.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from advisor_qa.world.instruments import load_instruments
from advisor_qa.world.market_calendar import (
    STRESS,
    TEST,
    TRAIN,
    VAL,
    assign_split,
    build_calendar,
)
from advisor_qa.world.price_model import DailyModel, simulate_daily
from advisor_qa.world.price_params import PriceParams, load_price_config

ETF_TYPES = {"equity_etf", "options_etf", "leveraged_etf"}


@pytest.fixture(scope="module")
def cfg() -> tuple[int, PriceParams]:
    return load_price_config()


# --- Calendar and regimes (test 8) -------------------------------------------------------------


def test_calendar_dates(cfg: tuple[int, PriceParams]) -> None:
    seed, p = cfg
    cal = build_calendar(p, seed)
    assert len(cal.days) == 180
    assert cal.days[0] == date(2026, 1, 2) and cal.days[-1] == date(2026, 9, 21)
    assert cal.days[45] == date(2026, 3, 10) and cal.fixture_index == 45  # trading day 46
    assert cal.days[44] == date(2026, 3, 9)
    assert cal.pre_day == date(2025, 12, 31)
    assert not set(p.holidays) & set(cal.days)
    assert all(d.weekday() < 5 for d in cal.days)


def test_split_is_contiguous_and_fixture_in_train(cfg: tuple[int, PriceParams]) -> None:
    seed, p = cfg
    cal = build_calendar(p, seed)
    assert cal.split == assign_split(180, 0.2, 0.2)
    assert cal.split.count(TEST) == 36 and cal.split.count(VAL) == 36
    assert cal.split[-36:] == [TEST] * 36 and cal.split[108:144] == [VAL] * 36
    assert cal.split[cal.fixture_index] == TRAIN


def test_regime_share_and_stress_days_per_split(cfg: tuple[int, PriceParams]) -> None:
    seed, p = cfg
    frame = build_calendar(p, seed).frame()
    stress = frame["regime"] == STRESS
    assert 0.08 <= stress.mean() <= 0.15
    for name in (TRAIN, VAL, TEST):
        assert stress[frame["split"] == name].sum() >= 5
    assert frame.loc[frame["is_fixture_day"], "regime"].tolist() == [STRESS]
    assert frame["is_fixture_day"].sum() == 1


def test_regime_resampling_raises_when_impossible(cfg: tuple[int, PriceParams]) -> None:
    seed, p = cfg
    impossible = p.model_copy(update={"min_stress_days_per_split": 40, "max_regime_tries": 3})
    with pytest.raises(RuntimeError, match="no regime path"):
        build_calendar(impossible, seed)


def test_regime_path_is_seeded(cfg: tuple[int, PriceParams]) -> None:
    seed, p = cfg
    a, b = build_calendar(p, seed), build_calendar(p, seed)
    assert np.array_equal(a.stress, b.stress) and a.regime_seed == b.regime_seed


# --- Market factor, ETFs and leveraged ETFs (tests 6, 8, 9) -----------------------------------


@pytest.fixture(scope="module")
def equity_daily(cfg: tuple[int, PriceParams]) -> tuple[pd.DataFrame, DailyModel]:
    seed, p = cfg
    inst = load_instruments()
    inst = inst[inst["instrument_type"].isin(ETF_TYPES)]
    return inst, simulate_daily(inst, p, seed, build_calendar(p, seed))


def _ids(inst: pd.DataFrame) -> dict[str, str]:
    return dict(zip(inst["ticker"], inst["instrument_id"], strict=True))


def test_leverage_is_exact_daily_multiple(equity_daily: tuple[pd.DataFrame, DailyModel]) -> None:
    inst, m = equity_daily
    ids = _ids(inst)
    spy, sso, tqqq = (m.returns[ids[t]][1:] for t in ("SPY", "SSO", "TQQQ"))
    tech = 1.2 * m.components["market"][1:] + m.components["tech"][1:]
    np.testing.assert_allclose(sso, 2 * spy, rtol=0, atol=1e-12)
    np.testing.assert_allclose(tqqq, 3 * tech, rtol=0, atol=1e-12)


def test_stress_to_calm_vol_ratio(
    cfg: tuple[int, PriceParams], equity_daily: tuple[pd.DataFrame, DailyModel]
) -> None:
    seed, p = cfg
    stress = build_calendar(p, seed).stress[1:]
    spy = equity_daily[1].returns[_ids(equity_daily[0])["SPY"]][1:]
    ratio = spy[stress].std(ddof=1) / spy[~stress].std(ddof=1)
    assert 2 <= ratio <= 4.5


def test_daily_moves_are_sane(equity_daily: tuple[pd.DataFrame, DailyModel]) -> None:
    inst, m = equity_daily
    for _, r in inst.iterrows():
        bound = 0.40 if r["instrument_type"] == "leveraged_etf" else 0.15
        assert np.abs(m.returns[r["instrument_id"]][1:]).max() < bound, r["ticker"]


def test_adding_an_instrument_does_not_change_the_others(
    cfg: tuple[int, PriceParams], equity_daily: tuple[pd.DataFrame, DailyModel]
) -> None:
    seed, p = cfg
    inst, m = equity_daily
    fewer = simulate_daily(inst[inst["ticker"] != "SCHD"], p, seed, build_calendar(p, seed))
    for sid, r in fewer.returns.items():
        assert np.array_equal(r, m.returns[sid])
