"""Intraday paths on the 5-minute grid: one Brownian bridge per component and day.

A component's path over the day is `x(s) = R*s + sigma*(W(s) - s*W(1))`, with s the fraction of the
day elapsed, so it starts at 0 and ends exactly at the day's value R. With extra anchors (the
fixture time) the bridge is piecewise: it passes through every anchor exactly. Series paths are the
same linear loadings of the component paths as their daily returns. All prices are synthetic.
"""

from datetime import datetime, timedelta

import numpy as np

from advisor_qa.world.market_calendar import Calendar
from advisor_qa.world.price_model import MARKET, TECH, DailyModel, Series
from advisor_qa.world.price_params import PriceParams
from advisor_qa.world.streams import stream

Anchors = dict[int, float]  # grid index -> value; the day's open (0) and close are added


def grid_times(p: PriceParams) -> list[str]:
    """'HH:MM' from grid_start to grid_end inclusive, every grid_step_minutes."""
    t = datetime.strptime(p.grid_start, "%H:%M")
    end = datetime.strptime(p.grid_end, "%H:%M")
    out: list[str] = []
    while t <= end:
        out.append(t.strftime("%H:%M"))
        t += timedelta(minutes=p.grid_step_minutes)
    if out[-1] != p.grid_end:
        raise ValueError("grid_end must fall on the grid")
    return out


def bridge(z: np.ndarray, sigma: float, close: float, anchors: Anchors | None = None) -> np.ndarray:
    """Brownian bridge on len(z) + 1 grid points from 0 to `close`, through any extra anchors.
    z are standard normal increments; W is scaled so W(1) has unit variance."""
    n = len(z)
    w = np.concatenate([[0.0], np.cumsum(z) / np.sqrt(n)])
    pts = {0: 0.0, **(anchors or {}), n: close}
    idx = np.array(sorted(pts), dtype=float)
    vals = np.array([pts[int(i)] for i in idx])
    grid = np.arange(n + 1, dtype=float)
    path: np.ndarray = np.interp(grid, idx, vals) + sigma * (
        w - np.interp(grid, idx, w[idx.astype(int)])
    )
    for i, v in pts.items():
        path[i] = v  # exact at every anchor, independent of floating-point rounding
    return path


def _sigmas(m: DailyModel, p: PriceParams, cal: Calendar) -> dict[str, np.ndarray]:
    """Intraday bridge volatility per component and day (share of the daily volatility)."""
    eq, bd = p.intraday_share_equity, p.intraday_share_bond
    mkt = np.where(cal.stress, p.market_vol_stress, p.market_vol_calm)
    yld = np.where(cal.stress, p.sim_yield_vol_stress, p.sim_yield_vol_calm)
    ones = np.ones(len(cal.all_days))
    out = {MARKET: eq * mkt, TECH: eq * p.tech_noise_vol * ones, "dy2": bd * yld, "dy10": bd * yld}
    out["carry2"] = out["carry10"] = 0 * ones  # deterministic: carry accrues linearly
    for name, vol in m.spec.idio_vol.items():
        out[name] = m.spec.idio_intraday_share[name] * vol * ones
    return out


class ComponentPaths:
    """Bridges for every component and day. Each (component, day) has its own random stream, so
    re-pinning one bridge reuses the same increments and leaves every other bridge unchanged."""

    def __init__(self, m: DailyModel, p: PriceParams, seed: int, cal: Calendar) -> None:
        self.m, self.seed, self.cal = m, seed, cal
        self.n_steps = len(grid_times(p)) - 1
        self.sigma = _sigmas(m, p, cal)
        self.paths: dict[str, np.ndarray] = {}
        for name in m.components:
            arr = np.full((len(cal.all_days), self.n_steps + 1), np.nan)
            for i in range(1, len(cal.all_days)):
                arr[i] = self._bridge(name, i, None)
            self.paths[name] = arr

    def _bridge(self, name: str, day_index: int, anchors: Anchors | None) -> np.ndarray:
        z = stream(self.seed, "bridge", name, self.cal.all_days[day_index])
        increments = z.standard_normal(self.n_steps)
        sigma = float(self.sigma[name][day_index])
        return bridge(increments, sigma, float(self.m.components[name][day_index]), anchors)

    def pin(self, name: str, day_index: int, anchors: Anchors) -> None:
        self.paths[name][day_index] = self._bridge(name, day_index, anchors)

    def series_path(self, s: Series) -> np.ndarray:
        """x(t) for a series: its loadings applied to the component paths. Row 0 is unused."""
        out = np.zeros((len(self.cal.all_days), self.n_steps + 1))
        for name, w in s.loadings.items():
            out = out + w * self.paths[name]
        out[0] = np.nan
        return out
