"""Acceptance tests for the synthetic price layer (docs/price_simulator_spec.md, section 6).

All prices are synthetic. FRED-mode tests use the made-up yield files in tests/fixtures/fred/,
never the real downloads.
"""

import ast
import filecmp
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from advisor_qa.world import fixture as fx
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
from advisor_qa.world.prices import PriceWorld, build_prices, main, read_price_table
from advisor_qa.world.yields import FRED, SIMULATED, carry_forward, read_fred_series
from tests.unit.test_fixture_household import DAY_MOVES

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
    assert ids[:-2] == list(load_instruments()["instrument_id"])
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
        if not s.is_mutual_fund and not s.foreign:
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


# --- Fixture day (test 2) -----------------------------------------------------------------------

FIXTURE_TS = "2026-03-10T14:05:00"


def fixture_impact(w: PriceWorld) -> float:
    """Sum of qty x (latent value at 14:05 - prior close) over HH-004217's holdings."""
    return sum(
        qty * (w.latent_value(ins, FIXTURE_TS) - w.prior_close(ins, "2026-03-10"))
        for _, ins, qty in fx.FIXTURE_HOLDINGS
    )


def test_fixture_moves_match_household_test(cfg: tuple[int, PriceParams]) -> None:
    assert cfg[1].fixture_moves == DAY_MOVES


@pytest.mark.parametrize("which", ["world", "world_fred"])
def test_fixture_true_impact(which: str, request: pytest.FixtureRequest) -> None:
    w: PriceWorld = request.getfixturevalue(which)
    impact = fixture_impact(w)
    assert impact == pytest.approx(-21_700, abs=0.01)
    assert impact / 1_200_000 == pytest.approx(-0.018, abs=0.0001)
    ref = load_instruments().set_index("instrument_id")["reference_price"]
    for ins, move in DAY_MOVES.items():
        assert w.prior_close(ins, "2026-03-10") == ref[ins]
        assert w.latent_value(ins, FIXTURE_TS) == pytest.approx(ref[ins] * (1 + move), abs=1e-6)


def test_fixture_day_is_d1_setup(world: PriceWorld) -> None:
    """Funds publish the 2026-03-09 NAV on the fixture day while the latent value has moved."""
    ref = load_instruments().set_index("instrument_id")["reference_price"]
    for ins in ("INS-05", "INS-14", "INS-15"):
        price, as_of = world.published_price(ins, FIXTURE_TS)
        assert price == ref[ins] and as_of == datetime(2026, 3, 9, 16, 0)
        assert world.latent_value(ins, FIXTURE_TS) != price


def test_fixture_close_is_simulated_and_others_follow(world: PriceWorld) -> None:
    i = world.calendar.all_days.index(date(2026, 3, 10))
    spy = world.paths["INS-01"][i]
    sso = world.paths["INS-20"][i]
    # SSO is not pinned but follows the pinned market factor: 2x SPY's move so far
    np.testing.assert_allclose(sso / sso[0] - 1, 2 * (spy / spy[0] - 1), atol=1e-6)
    # the close is drawn, not scripted
    assert world.closes["INS-01"][i] / 450 - 1 != pytest.approx(-0.021, abs=1e-6)


# --- CSV writer, CLI and determinism (test 1) ---------------------------------------------------

FILES = ["calendar.csv", "prices_daily.csv", "prices_published.csv", "prices_latent.csv"]
COLUMNS = {
    "calendar.csv": ["market_date", "regime", "split", "is_fixture_day"],
    "prices_daily.csv": [
        "instrument_id", "market_date", "prior_close", "close", "price_type", "close_as_of",
    ],
    "prices_published.csv": ["instrument_id", "market_date", "time", "price", "price_as_of"],
    "prices_latent.csv": ["instrument_id", "market_date", "time", "value"],
    "events.csv": ["instrument_id", "market_date", "type", "ratio"],
}  # fmt: skip


def _run_cli(monkeypatch: pytest.MonkeyPatch, out: Path, *extra: str) -> None:
    monkeypatch.setattr(sys, "argv", ["prices", "--out", str(out), *extra])
    main()


@pytest.mark.parametrize("extra", [(), ("--fred-dir", str(FAKE_FRED))], ids=["simulated", "fred"])
def test_same_seed_gives_byte_identical_csvs(
    extra: tuple[str, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_cli(monkeypatch, tmp_path / "a", *extra)
    _run_cli(monkeypatch, tmp_path / "b", *extra)
    names = [*FILES, "events.csv", "README.md"]
    match, mismatch, errors = filecmp.cmpfiles(tmp_path / "a", tmp_path / "b", names, shallow=False)
    assert match == names and not mismatch and not errors


def test_different_seed_gives_different_prices(
    cfg: tuple[int, PriceParams], world: PriceWorld
) -> None:
    seed, p = cfg
    other = build_prices(load_instruments(), p, seed + 1)
    assert not np.array_equal(other.paths["INS-04"], world.paths["INS-04"], equal_nan=True)


def test_csv_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, world: PriceWorld) -> None:
    _run_cli(monkeypatch, tmp_path)
    for name, cols in COLUMNS.items():
        first = (tmp_path / name).read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("# SYNTHETIC PRICES"), name
        assert list(read_price_table(tmp_path / name).columns) == cols, name
    assert "ALL PRICES IN THIS FOLDER ARE SYNTHETIC" in (tmp_path / "README.md").read_text()
    pub = read_price_table(tmp_path / "prices_published.csv").set_index(["instrument_id", "time"])
    lat = read_price_table(tmp_path / "prices_latent.csv").set_index(["instrument_id", "time"])
    daily = read_price_table(tmp_path / "prices_daily.csv")
    assert len(pub) == len(lat) == 24 * 180 * 79 and len(daily) == 24 * 180
    key = ("INS-05", "2026-03-10T14:05:00")
    assert pub.loc[key, "price"] == 100.0 and pub.loc[key, "price_as_of"] == "2026-03-09T16:00:00"
    assert lat.loc[key, "value"] == world.latent_value(*key) == 98.0
    etf = ("INS-01", "2026-03-10T14:05:00")
    assert pub.loc[etf, "price"] == lat.loc[etf, "value"] == 440.55
    assert pub.loc[etf, "price_as_of"] == "2026-03-10T14:05:00"
    funds = set(load_instruments().query("is_mutual_fund")["instrument_id"])
    assert set(daily.loc[daily["price_type"] == "nav", "instrument_id"]) == funds


# --- Separation (test 10) -----------------------------------------------------------------------

SRC = Path(__file__).resolve().parents[2] / "src" / "advisor_qa"
GUARDED_PARTS = {"checks", "scoring"}  # advisor_qa.checks, the scoring code, wherever it lives
LATENT_MODULE = "advisor_qa.world.latent"
LATENT_NAMES = {"latent_value", "latent_frame", "LATENT_FILE", "LATENT_COLUMNS"}


def latent_uses(source: str) -> list[str]:
    """Every way a module reaches the latent prices: importing the latent module, naming
    latent_value (or the latent writer), or the latent file name / module path as a string."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.startswith(LATENT_MODULE)]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith(LATENT_MODULE) or mod.split(".")[-1] == "latent":
                found.append(mod)
            found += [a.name for a in node.names if a.name in LATENT_NAMES | {"latent"}]
        elif isinstance(node, ast.Name) and node.id in LATENT_NAMES:
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in LATENT_NAMES:
            found.append(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "prices_latent" in node.value or LATENT_MODULE in node.value:
                found.append(node.value)
    return found


def guarded_files() -> list[Path]:
    return sorted(
        f
        for f in SRC.rglob("*.py")
        if GUARDED_PARTS & ({*f.relative_to(SRC).parts[:-1]} | {f.stem})
    )


def test_checks_and_scoring_never_use_latent_prices() -> None:
    for f in guarded_files():
        assert latent_uses(f.read_text(encoding="utf-8")) == [], f


@pytest.mark.parametrize(
    "source",
    [
        "import advisor_qa.world.latent",
        "from advisor_qa.world.latent import latent_frame",
        "from advisor_qa.world import latent",
        "from ..world import latent",
        "v = world.latent_value('INS-05', ts)",
        "df = pd.read_csv('data/synthetic/prices/prices_latent.csv')",
        "importlib.import_module('advisor_qa.world.latent')",
    ],
)
def test_separation_detector_catches_latent_use(source: str) -> None:
    assert latent_uses(source)


def test_separation_detector_allows_published_prices() -> None:
    ok = "from advisor_qa.world.prices import read_price_table\nx = w.published_price('INS-05', t)"
    assert latent_uses(ok) == []


def test_guarded_files_include_future_checks_and_scoring_packages() -> None:
    def guarded(rel: str) -> bool:
        f = SRC / rel
        return bool(GUARDED_PARTS & ({*f.relative_to(SRC).parts[:-1]} | {f.stem}))

    assert guarded("checks/d1.py") and guarded("scoring/weighted.py") and guarded("eval/scoring.py")
    assert not guarded("truth/impact.py") and not guarded("inject/d1.py")


def test_published_price_never_returns_latent_for_funds(world: PriceWorld) -> None:
    for s in world.series:
        if not s.is_mutual_fund or s.ticker == "VMFXX":
            continue
        for d in world.calendar.days:
            for t in world.times[1:]:
                ts = f"{d}T{t}:00"
                latent = world.latent_value(s.series_id, ts)
                if latent != world.prior_close(s.series_id, d):
                    assert world.published_price(s.series_id, ts)[0] != latent


def test_foreign_listing_publishes_home_close(world: PriceWorld) -> None:
    """D6 setup: during US hours the feed shows the home close (stamped 01:00 New York time)
    while the fair value moves with the US market."""
    s = next(s for s in world.series if s.foreign)
    price, as_of = world.published_price(s.series_id, "2026-03-10T14:05:00")
    assert as_of == datetime(2026, 3, 10, 1, 0)
    assert price == world.prior_close(s.series_id, "2026-03-10")
    assert world.latent_value(s.series_id, "2026-03-10T14:05:00") != price
