"""Price-layer parameters, read from the `prices` section of config/default.yaml.

Same mechanism as `params.py` uses for `households`. Each value in the YAML is marked calibrated,
design or assumption (see docs/price_simulator_spec.md, section 5). All prices are synthetic.
"""

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

from advisor_qa.world.params import DEFAULT_CONFIG


class ProxySeries(BaseModel):
    """A proxy that is not in instruments.csv. Written as `PROXY-<ticker>`, never held."""

    kind: str  # "equity" (beta on the market factor) or "muni" (rate sensitivity x duration)
    idio_vol: float
    beta: float = 1.0
    duration: float = 0.0
    rate_sensitivity: float = 1.0


class Leverage(BaseModel):
    multiple: float
    underlying: str  # "market" or "tech"


class PriceParams(BaseModel):
    # Calendar and grid
    start_date: date = date(2026, 1, 2)
    n_days: int = 180
    holidays: list[date] = []
    grid_start: str = "09:30"
    grid_end: str = "16:00"
    grid_step_minutes: int = 5
    fixture_date: date = date(2026, 3, 10)
    fixture_query_time: str = "14:05"
    foreign_home_close_time: str = "01:00"  # New York time of the home-market close (D6)
    anchor_date: date = date(2026, 3, 9)
    # Date split
    val_date_fraction: float = 0.2
    test_date_fraction: float = 0.2
    # Regimes
    p_calm_to_stress: float = 0.015
    p_stress_to_calm: float = 0.12
    min_stress_share: float = 0.08
    max_stress_share: float = 0.15
    min_stress_days_per_split: int = 5
    max_regime_tries: int = 1000
    # Market and tech factors
    market_ticker: str = "SPY"
    market_vol_calm: float = 0.0075
    market_vol_stress: float = 0.024
    market_drift_calm: float = 0.0004
    market_drift_stress: float = -0.0015
    market_t_df: float = 5.0
    tech_beta: float = 1.2
    tech_noise_vol: float = 0.005
    intraday_share_equity: float = 0.6
    intraday_share_bond: float = 0.3
    # Per-instrument rules, keyed by ticker
    betas: dict[str, float] = {}
    idio_vol: dict[str, float] = {}
    leverage: dict[str, Leverage] = {}
    target_date_equity_weight: dict[str, float] = {}
    target_date_bond_ticker: str = "BND"
    proxy_series: dict[str, ProxySeries] = {}
    proxy_reference_price: float = 100.0
    long_duration_threshold_years: float = 3.0
    # Yields
    sim_yield_vol_calm: float = 0.0004
    sim_yield_vol_stress: float = 0.0008
    sim_bond_equity_corr_calm: float = -0.15
    sim_bond_equity_corr_stress: float = -0.35
    sim_yield_2y_10y_corr: float = 0.8
    sim_start_yield_2y_pct: float = 3.5
    sim_start_yield_10y_pct: float = 4.2
    # Fixture day (moves keyed by instrument_id, from tests/unit/test_fixture_household.py)
    fixture_moves: dict[str, float] = {}
    # Sanity bounds on daily moves
    max_daily_move_unleveraged: float = 0.15
    max_daily_move_leveraged: float = 0.40

    @model_validator(mode="after")
    def _check(self) -> "PriceParams":
        if self.val_date_fraction + self.test_date_fraction >= 1:
            raise ValueError("val_date_fraction + test_date_fraction must be below 1")
        if not 0 < self.p_calm_to_stress < 1 or not 0 < self.p_stress_to_calm < 1:
            raise ValueError("regime switch probabilities must be in (0, 1)")
        for name, ps in self.proxy_series.items():
            if ps.kind not in {"equity", "muni"}:
                raise ValueError(f"proxy {name}: kind must be equity or muni")
        for name, lev in self.leverage.items():
            if lev.underlying not in {"market", "tech"}:
                raise ValueError(f"leverage {name}: underlying must be market or tech")
        return self


def load_price_config(path: Path = DEFAULT_CONFIG) -> tuple[int, PriceParams]:
    """Returns (seed, price params). `n_days` and `test_date_fraction` must agree with the
    top-level `n_days` and `split.test_date_fraction`, so the two layers cannot drift apart."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    p = PriceParams(**raw.get("prices", {}))
    if "n_days" in raw and int(raw["n_days"]) != p.n_days:
        raise ValueError(f"prices.n_days {p.n_days} != top-level n_days {raw['n_days']}")
    top_test = raw.get("split", {}).get("test_date_fraction")
    if top_test is not None and float(top_test) != p.test_date_fraction:
        raise ValueError("prices.test_date_fraction must equal split.test_date_fraction")
    return int(raw["seed"]), p
