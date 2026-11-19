"""Factor model for the synthetic price layer. All prices are synthetic.

Every series' daily return is a fixed linear combination ("loadings") of components:

- `market`: the market factor r_SPY (Student-t, calm/stress volatility and drift)
- `tech`: tech-factor noise; r_tech = tech_beta x market + tech (TQQQ's underlying)
- `idio:<series_id>`: the series' own idiosyncratic term

The intraday paths (step 5) are combined with the same loadings, so related series move together
and every path ends exactly at its daily return.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from advisor_qa.world.market_calendar import Calendar
from advisor_qa.world.price_params import PriceParams
from advisor_qa.world.streams import stream

MARKET, TECH = "market", "tech"

Loadings = dict[str, float]


def idio(series_id: str) -> str:
    return f"idio:{series_id}"


def _combine(*parts: tuple[float, Loadings]) -> Loadings:
    out: Loadings = {}
    for w, loadings in parts:
        for k, v in loadings.items():
            out[k] = out.get(k, 0.0) + w * v
    return out


@dataclass(frozen=True)
class Series:
    series_id: str  # instrument_id, or PROXY-<ticker> for a proxy not in instruments.csv
    ticker: str
    is_mutual_fund: bool
    leveraged: bool
    reference_price: float
    loadings: Loadings


@dataclass(frozen=True)
class ModelSpec:
    series: list[Series]
    idio_vol: dict[str, float]  # component name -> daily vol
    idio_intraday_share: dict[str, float]  # component name -> intraday share of daily vol


class _SpecBuilder:
    def __init__(self, inst: pd.DataFrame, p: PriceParams) -> None:
        self.p = p
        self.rows = {str(r["ticker"]): r for _, r in inst.iterrows()}
        self.loadings: dict[str, Loadings] = {}
        self.idio_vol: dict[str, float] = {}
        self.idio_share: dict[str, float] = {}

    def _own_idio(self, series_id: str, ticker: str, bond: bool) -> Loadings:
        if ticker not in self.p.idio_vol:
            raise ValueError(f"prices.idio_vol has no entry for {ticker}")
        name = idio(series_id)
        self.idio_vol[name] = self.p.idio_vol[ticker]
        p = self.p
        self.idio_share[name] = p.intraday_share_bond if bond else p.intraday_share_equity
        return {name: 1.0}

    def for_ticker(self, ticker: str) -> Loadings:
        if ticker not in self.loadings:
            if ticker not in self.rows:
                raise ValueError(f"no rule for ticker {ticker}")
            self.loadings[ticker] = self._instrument(self.rows[ticker])
        return self.loadings[ticker]

    def _instrument(self, r: pd.Series) -> Loadings:
        p, ticker, sid = self.p, str(r["ticker"]), str(r["instrument_id"])
        if ticker == p.market_ticker:
            return {MARKET: 1.0}
        if ticker in p.leverage:
            lev = p.leverage[ticker]
            under = {MARKET: 1.0} if lev.underlying == "market" else self.tech()
            return _combine((lev.multiple, under))
        if ticker not in p.betas:
            raise ValueError(f"prices.betas has no entry for {ticker}")
        return _combine((p.betas[ticker], {MARKET: 1.0}), (1.0, self._own_idio(sid, ticker, False)))

    def tech(self) -> Loadings:
        return {MARKET: self.p.tech_beta, TECH: 1.0}


def build_spec(inst: pd.DataFrame, p: PriceParams) -> ModelSpec:
    """One Series per instrument, in instruments.csv order."""
    b = _SpecBuilder(inst, p)
    series = [
        Series(
            series_id=str(r["instrument_id"]),
            ticker=str(r["ticker"]),
            is_mutual_fund=bool(r["is_mutual_fund"]),
            leveraged=str(r["ticker"]) in p.leverage,
            reference_price=float(r["reference_price"]),
            loadings=b.for_ticker(str(r["ticker"])),
        )
        for _, r in inst.iterrows()
    ]
    return ModelSpec(series, b.idio_vol, b.idio_share)


def market_daily(p: PriceParams, seed: int, cal: Calendar) -> tuple[np.ndarray, np.ndarray]:
    """(market return, standardised shock), length n + 1 (index 0 = pre-window day, unused)."""
    df = p.market_t_df
    shock = np.zeros(len(cal.all_days))
    for i, d in enumerate(cal.all_days[1:], start=1):
        shock[i] = stream(seed, "market", d).standard_t(df) / np.sqrt(df / (df - 2))
    vol = np.where(cal.stress, p.market_vol_stress, p.market_vol_calm)
    drift = np.where(cal.stress, p.market_drift_stress, p.market_drift_calm)
    ret = drift + vol * shock
    ret[0] = 0.0
    return ret, shock


def normal_daily(seed: int, purpose: str, key: str, cal: Calendar, vol: float) -> np.ndarray:
    out = np.zeros(len(cal.all_days))
    for i, d in enumerate(cal.all_days[1:], start=1):
        out[i] = vol * stream(seed, purpose, key, d).standard_normal()
    return out


@dataclass(frozen=True)
class DailyModel:
    spec: ModelSpec
    components: dict[str, np.ndarray]  # component -> daily value, index 0 = pre-window day
    returns: dict[str, np.ndarray]  # series_id -> daily return, index 0 = 0


def daily_returns(spec: ModelSpec, components: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    n = len(next(iter(components.values())))
    out: dict[str, np.ndarray] = {}
    for s in spec.series:
        r = np.zeros(n)
        for name, w in s.loadings.items():
            r = r + w * components[name]
        out[s.series_id] = r
    return out


def simulate_daily(inst: pd.DataFrame, p: PriceParams, seed: int, cal: Calendar) -> DailyModel:
    spec = build_spec(inst, p)
    market, _ = market_daily(p, seed, cal)
    comps: dict[str, np.ndarray] = {
        MARKET: market,
        TECH: normal_daily(seed, "tech", TECH, cal, p.tech_noise_vol),
    }
    for name, vol in spec.idio_vol.items():
        comps[name] = normal_daily(seed, "idio", name, cal, vol)
    return DailyModel(spec, comps, daily_returns(spec, comps))
