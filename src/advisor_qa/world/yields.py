"""Treasury yields that drive the synthetic bond returns.

With a FRED directory, read DGS2.csv and DGS10.csv (Board of Governors of the Federal Reserve
System, retrieved from FRED, Federal Reserve Bank of St. Louis). Blank values (newer downloads) and
"." (older downloads) are missing, and the previous value is carried forward. FRED yields are in
percent; changes are divided by 100 before duration is applied. FRED-driven bond returns are
independent of the simulated equities (no flight-to-quality effect).

Without FRED files the yield changes are simulated: calm/stress volatility and a bond-equity
correlation, defined as corr(bond return, r_SPY), i.e. corr(yield change, r_SPY) has the
opposite sign. Bond prices are synthetic either way.
"""

import bisect
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from advisor_qa.world.market_calendar import Calendar
from advisor_qa.world.price_params import PriceParams
from advisor_qa.world.streams import stream

log = logging.getLogger(__name__)

TENOR_2Y, TENOR_10Y = "2y", "10y"
FRED_FILES = {TENOR_2Y: "DGS2.csv", TENOR_10Y: "DGS10.csv"}
FRED, SIMULATED = "fred", "simulated"
MISSING = {"", "."}


@dataclass(frozen=True)
class YieldPaths:
    source: str  # "fred" or "simulated"
    level_pct: dict[str, np.ndarray]  # tenor -> yield in percent on cal.all_days

    def change(self, tenor: str) -> np.ndarray:
        """Daily yield change as a decimal (0.0004 = 4 bp); index 0 is 0."""
        lv = self.level_pct[tenor]
        return np.concatenate([[0.0], np.diff(lv) / 100])

    def carry(self, tenor: str) -> np.ndarray:
        """One day of carry, previous close's yield / 252, as a decimal; index 0 is 0."""
        lv = self.level_pct[tenor]
        return np.concatenate([[0.0], lv[:-1] / 100 / 252])


def read_fred_series(path: Path) -> pd.Series:
    """A FRED download (date column, value column) as a float Series indexed by date. Blank and
    "." values are NaN; anything else that is not a number is an error."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if df.shape[1] != 2:
        raise ValueError(f"{path}: expected a date column and one value column")
    raw = df.iloc[:, 1].str.strip()
    bad = ~raw.isin(MISSING) & pd.to_numeric(raw, errors="coerce").isna()
    if bad.any():
        raise ValueError(f"{path}: non-numeric values {sorted(set(raw[bad]))[:5]}")
    values = pd.to_numeric(raw.where(~raw.isin(MISSING)), errors="coerce").astype(float)
    dates = pd.to_datetime(df.iloc[:, 0].str.strip()).dt.date
    return pd.Series(values.to_numpy(), index=dates.to_numpy()).sort_index()


def carry_forward(series: pd.Series, days: list[date], name: str) -> np.ndarray:
    """Last non-missing observation on or before each day."""
    obs = series.dropna()
    obs_days: list[date] = list(obs.index)
    if not obs_days or obs_days[0] > days[0]:
        raise ValueError(f"{name}: no observation on or before {days[0]}")
    if obs_days[-1] < days[-1]:
        log.warning("%s ends %s; carrying it forward to %s", name, obs_days[-1], days[-1])
    vals = obs.to_numpy(dtype=float)
    return np.array([vals[bisect.bisect_right(obs_days, d) - 1] for d in days])


def fred_yields(fred_dir: Path, cal: Calendar) -> YieldPaths:
    levels: dict[str, np.ndarray] = {}
    for tenor, fname in FRED_FILES.items():
        path = fred_dir / fname
        if not path.exists():
            raise FileNotFoundError(f"{path} not found (pass no --fred-dir to simulate yields)")
        levels[tenor] = carry_forward(read_fred_series(path), cal.all_days, fname)
    return YieldPaths(FRED, levels)


def simulated_yields(
    p: PriceParams, seed: int, cal: Calendar, market_shock: np.ndarray
) -> YieldPaths:
    """Yield changes with calm/stress volatility, correlated with the market shock. A negative
    bond-equity correlation means yields fall when the market falls (flight to quality)."""
    stress = cal.stress
    vol = np.where(stress, p.sim_yield_vol_stress, p.sim_yield_vol_calm)
    rho = -np.where(stress, p.sim_bond_equity_corr_stress, p.sim_bond_equity_corr_calm)
    c = p.sim_yield_2y_10y_corr
    e10, e2 = np.zeros(len(cal.all_days)), np.zeros(len(cal.all_days))
    for i, d in enumerate(cal.all_days[1:], start=1):
        e10[i] = stream(seed, "yield", TENOR_10Y, d).standard_normal()
        e2[i] = stream(seed, "yield", TENOR_2Y, d).standard_normal()
    resid = np.sqrt(1 - rho**2)
    dy10 = vol * (rho * market_shock + resid * e10)
    dy2 = vol * (rho * market_shock + resid * (c * e10 + np.sqrt(1 - c**2) * e2))
    dy10[0] = dy2[0] = 0.0
    return YieldPaths(
        SIMULATED,
        {
            TENOR_2Y: p.sim_start_yield_2y_pct + np.cumsum(dy2) * 100,
            TENOR_10Y: p.sim_start_yield_10y_pct + np.cumsum(dy10) * 100,
        },
    )
