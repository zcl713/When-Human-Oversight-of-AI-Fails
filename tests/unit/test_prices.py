"""Acceptance tests for the synthetic price layer (docs/price_simulator_spec.md, section 6).

All prices are synthetic. FRED-mode tests use the made-up yield files in tests/fixtures/fred/,
never the real downloads.
"""

from datetime import date, datetime
from pathlib import Path

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
from advisor_qa.world.prices import PriceWorld, build_prices
from advisor_qa.world.yields import FRED, SIMULATED, carry_forward, read_fred_series

ETF_TYPES = {"equity_etf", "options_etf", "leveraged_etf"}
FAKE_FRED = Path(__file__).parent.parent / "fixtures" / "fred"  # made-up yields, not FRED data


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


# --- Bonds and yields (test 7) ------------------------------------------------------------------


def _etfs(cfg: tuple[int, PriceParams], fred_dir: Path | None) -> tuple[pd.DataFrame, DailyModel]:
    seed, p = cfg
    inst = load_instruments()
    inst = inst[~inst["is_mutual_fund"]]
    return inst, simulate_daily(inst, p, seed, build_calendar(p, seed), fred_dir)


def test_fred_loader_carries_blank_and_dot_forward() -> None:
    for name in ("DGS2.csv", "DGS10.csv"):
        s = read_fred_series(FAKE_FRED / name)
        gaps = s[s.isna()]
        assert len(gaps) == 5
        days = list(s.index)
        filled = carry_forward(s, days, name)
        for d in gaps.index:
            i = days.index(d)
            assert filled[i] == filled[i - 1] and not np.isnan(filled[i])


def test_fred_loader_rejects_window_before_first_observation() -> None:
    s = read_fred_series(FAKE_FRED / "DGS10.csv")
    with pytest.raises(ValueError, match="no observation"):
        carry_forward(s, [date(2025, 1, 2)], "DGS10.csv")


def test_bond_tracks_ten_year_yield_with_fred(cfg: tuple[int, PriceParams]) -> None:
    inst, m = _etfs(cfg, FAKE_FRED)
    assert m.yields.source == FRED
    ids = _ids(inst)
    dy10 = m.yields.change("10y")[1:]
    bnd = m.returns[ids["BND"]][1:]
    assert np.corrcoef(bnd, -dy10)[0, 1] >= 0.9
    # changes are divided by 100: a 10 bp move is about -0.58% on BND before carry and idio
    assert np.abs(dy10).max() < 0.01
    bsv = m.returns[ids["BSV"]][1:]
    assert np.corrcoef(bsv, -m.yields.change("2y")[1:])[0, 1] >= 0.8  # shorter duration


def test_simulated_yields_without_fred(cfg: tuple[int, PriceParams]) -> None:
    inst, m = _etfs(cfg, None)
    assert m.yields.source == SIMULATED
    bnd = m.returns[_ids(inst)["BND"]][1:]
    assert np.isfinite(bnd).all() and bnd.std() > 0
    # flight to quality: bond returns are negatively correlated with the market
    assert np.corrcoef(bnd, m.components["market"][1:])[0, 1] < 0


def test_missing_fred_file_is_an_error(cfg: tuple[int, PriceParams], tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _etfs(cfg, tmp_path)


# --- Mutual funds and proxies (tests 4, 5) ------------------------------------------------------

PROXY_TIE = {  # fund ticker -> (proxy series ticker, min corr, max corr)
    "VFIAX": ("SPY", 0.98, 1.0),
    "VBTLX": ("BND", 0.98, 1.0),
    "VBIRX": ("BSV", 0.98, 1.0),
    "FCNTX": ("VUG", 0.95, 0.99),
    "AGTHX": ("VUG", 0.95, 0.99),
    "VWIUX": ("MUB", 0.75, 0.95),
}


def _all(cfg: tuple[int, PriceParams], fred_dir: Path | None) -> DailyModel:
    seed, p = cfg
    return simulate_daily(load_instruments(), p, seed, build_calendar(p, seed), fred_dir)


def test_proxy_series_ids() -> None:
    _, p = load_price_config()
    ids = [s.series_id for s in _all((0, p), None).spec.series]
    assert ids[-2:] == ["PROXY-MUB", "PROXY-VUG"]
    assert ids[:21] == list(load_instruments()["instrument_id"])
    assert not any(i in ids for i in ("PROXY-SPY", "PROXY-BND", "PROXY-BSV"))


@pytest.mark.parametrize("fred_dir", [FAKE_FRED, None], ids=["fred", "simulated"])
def test_proxy_tie(cfg: tuple[int, PriceParams], fred_dir: Path | None) -> None:
    m = _all(cfg, fred_dir)
    by_ticker = {s.ticker: s.series_id for s in m.spec.series}
    for fund, (proxy, lo, hi) in PROXY_TIE.items():
        r_fund, r_proxy = m.returns[by_ticker[fund]][1:], m.returns[by_ticker[proxy]][1:]
        corr = np.corrcoef(r_fund, r_proxy)[0, 1]
        assert lo <= corr <= hi, (fund, proxy, corr)


def test_money_market_and_target_date(cfg: tuple[int, PriceParams]) -> None:
    m = _all(cfg, None)
    by_ticker = {s.ticker: s.series_id for s in m.spec.series}
    assert np.array_equal(m.returns[by_ticker["VMFXX"]], np.zeros(181))
    spy, bnd = m.returns[by_ticker["SPY"]][1:], m.returns[by_ticker["BND"]][1:]
    blend = 0.9 * spy + 0.1 * bnd
    r = m.returns[by_ticker["VFIFX"]][1:]
    assert np.abs(r - blend).max() < 0.005  # equity weight 0.90 plus a small idio


# --- Intraday paths, anchoring and publishing (tests 3, 4) -------------------------------------


@pytest.fixture(scope="module")
def world(cfg: tuple[int, PriceParams]) -> PriceWorld:
    seed, p = cfg
    return build_prices(load_instruments(), p, seed)


@pytest.fixture(scope="module")
def world_fred(cfg: tuple[int, PriceParams]) -> PriceWorld:
    seed, p = cfg
    return build_prices(load_instruments(), p, seed, FAKE_FRED)


@pytest.mark.parametrize("which", ["world", "world_fred"])
def test_path_integrity(which: str, request: pytest.FixtureRequest) -> None:
    w: PriceWorld = request.getfixturevalue(which)
    assert len(w.times) == 79 and w.times[0] == "09:30" and w.times[-1] == "16:00"
    for s in w.series:
        path, close = w.paths[s.series_id][1:], w.closes[s.series_id]
        assert not np.isnan(path).any() and not np.isnan(close).any(), s.series_id
        assert (path > 0).all() and (close > 0).all(), s.series_id
        assert np.array_equal(path[:, 0], close[:-1]), s.series_id
        assert np.array_equal(path[:, -1], close[1:]), s.series_id


def test_anchor_close_equals_reference_price(world: PriceWorld) -> None:
    for s in world.series:
        assert world.prior_close(s.series_id, "2026-03-10") == s.reference_price, s.series_id
        assert world.latent_value(s.series_id, "2026-03-09T16:00:00") == s.reference_price


def test_fund_published_price_is_previous_nav(world: PriceWorld) -> None:
    days = world.calendar.days
    funds = [s for s in world.series if s.is_mutual_fund]
    assert len(funds) == int(load_instruments()["is_mutual_fund"].sum()) == 11
    for s in funds:
        for i, d in enumerate(days[1:], start=1):
            nav = world.prior_close(s.series_id, d)
            for t in world.times:
                price, as_of = world.published_price(s.series_id, f"{d}T{t}:00")
                assert price == nav and as_of == datetime.fromisoformat(f"{days[i - 1]}T16:00:00")
        # never the latent value: published stays flat while the latent value moves
        latent = world.paths[s.series_id][1:]
        if s.ticker != "VMFXX":
            assert (latent[:, 1:] != latent[:, :1]).any()


def test_money_market_is_one_dollar(world: PriceWorld) -> None:
    vmfxx = next(s.series_id for s in world.series if s.ticker == "VMFXX")
    assert (world.closes[vmfxx] == 1.0).all() and (world.paths[vmfxx][1:] == 1.0).all()
    assert world.published_price(vmfxx, "2026-06-01T11:00:00")[0] == 1.0


def test_etf_published_equals_latent(world: PriceWorld) -> None:
    for s in world.series:
        if not s.is_mutual_fund:
            ts = "2026-03-10T11:17:42"
            price, as_of = world.published_price(s.series_id, ts)
            assert price == world.latent_value(s.series_id, ts)
            assert as_of == datetime(2026, 3, 10, 11, 15)


def test_query_times_snap_down(world: PriceWorld) -> None:
    spy, i = "INS-01", world.calendar.all_days.index(date(2026, 3, 10))
    assert world.locate("2026-03-10T14:09:59") == (i, 55)
    assert world.locate("2026-03-10T16:30:00") == (i, 78)
    assert world.locate("2026-03-10T09:29:00") == (i - 1, 78)  # before the open: prior close
    assert world.locate("2026-03-14T12:00:00") == (
        world.calendar.all_days.index(date(2026, 3, 13)),
        78,
    )
    assert world.latent_value(spy, "2026-01-02T09:00:00") == world.prior_close(spy, "2026-01-02")
    with pytest.raises(ValueError):
        world.latent_value(spy, "2025-12-31T12:00:00")
    with pytest.raises(ValueError):
        world.latent_value(spy, "2026-09-22T10:00:00")
    with pytest.raises(ValueError):
        world.published_price("INS-05", "2026-01-02T08:00:00")
    with pytest.raises(KeyError):
        world.latent_value("INS-99", "2026-03-10T10:00:00")


def test_anchoring_keeps_model_returns(cfg: tuple[int, PriceParams], world: PriceWorld) -> None:
    m = _all(cfg, None)
    for s in world.series:
        c = world.closes[s.series_id]
        np.testing.assert_allclose(c[1:] / c[:-1] - 1, m.returns[s.series_id][1:], atol=1e-7)
