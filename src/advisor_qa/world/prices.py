"""Synthetic price layer. ALL PRICES ARE SYNTHETIC: simulated, not market data.

`uv run python -m advisor_qa.world.prices --out data/synthetic/prices [--fred-dir data/raw/fred]`

Design: docs/price_simulator_spec.md. Daily returns come from a factor model (price_model.py), the
intraday path is a Brownian bridge per component (intraday.py), and each series is scaled so its
close on the anchor date equals its `reference_price`. ETFs publish their latent value; a mutual
fund publishes the previous day's NAV (stamped 16:00 that day) at every grid time, which is what
makes the stale-NAV case (D1) possible.

`latent_value` and prices_latent.csv are for the truth function and error injectors only. Checks
and scoring must never use them (tests/unit/test_prices.py enforces this).
"""

import argparse
import bisect
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from advisor_qa.world import fixture as fx
from advisor_qa.world.instruments import load_instruments
from advisor_qa.world.intraday import ComponentPaths, grid_times
from advisor_qa.world.latent import LATENT_FILE, latent_frame
from advisor_qa.world.market_calendar import STRESS, Calendar, build_calendar
from advisor_qa.world.params import DEFAULT_CONFIG, REPO_ROOT
from advisor_qa.world.price_model import MARKET, ModelSpec, Series, idio, simulate_daily
from advisor_qa.world.price_params import PriceParams, load_price_config

log = logging.getLogger(__name__)

DECIMALS = 6  # prices are rounded once, so CSVs and PriceWorld hold identical numbers
SYNTHETIC_HEADER = "# SYNTHETIC PRICES - simulated, not market data. See README.md."
CALENDAR_FILE, DAILY_FILE = "calendar.csv", "prices_daily.csv"
PUBLISHED_FILE, EVENTS_FILE = "prices_published.csv", "events.csv"
EVENTS_COLUMNS = ["instrument_id", "market_date", "type", "ratio"]  # D4 hook, filled by injector


def _ts(d: date, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{d.isoformat()}T{hhmm}:00")


@dataclass(frozen=True)
class PriceWorld:
    """Synthetic prices for every series on the trading calendar, keyed by series id."""

    calendar: Calendar
    series: list[Series]
    times: list[str]  # grid times 'HH:MM', New York time
    closes: dict[str, np.ndarray]  # length n + 1; index 0 is the pre-window day
    paths: dict[str, np.ndarray]  # (n + 1, len(times)); row 0 (pre-window day) is NaN
    yield_source: str  # "fred" or "simulated"

    def grid_keys(self) -> tuple[list[str], list[str]]:
        """(market_date, time) for every window day and grid time, day-major, as written in the
        CSVs ('2026-03-10', '2026-03-10T14:05:00')."""
        dates: list[str] = []
        times: list[str] = []
        for d in self.calendar.days:
            iso = d.isoformat()
            dates.extend([iso] * len(self.times))
            times.extend(f"{iso}T{t}:00" for t in self.times)
        return dates, times

    def _get(self, series_id: str) -> Series:
        for s in self.series:
            if s.series_id == series_id:
                return s
        raise KeyError(f"unknown instrument_id {series_id}")

    def locate(self, ts: datetime | str) -> tuple[int, int]:
        """(index into calendar.all_days, grid index) of the latest grid point at or before ts.
        Before the open snaps to the previous trading day's close; a weekend or holiday to the
        last trading day's close. Index 0 (pre-window day) only has its close."""
        t = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
        days, last = self.calendar.all_days, len(self.times) - 1
        if t.date() > days[-1]:
            raise ValueError(f"{t} is after the price window (last day {days[-1]})")
        i = bisect.bisect_right(days, t.date()) - 1
        if i < 0:
            raise ValueError(f"{t} is before the price window")
        j = last
        if days[i] == t.date():
            open_ = _ts(days[i], self.times[0])
            if t < open_:
                i, j = i - 1, last
            else:
                step = (_ts(days[i], self.times[1]) - open_).total_seconds()
                j = min(int((t - open_).total_seconds() // step), last)
        if i < 0 or (i == 0 and j != last):
            raise ValueError(f"{t} is before the price window")
        return i, j

    def _value(self, series_id: str, i: int, j: int) -> float:
        if i == 0:
            return float(self.closes[series_id][0])
        return float(self.paths[series_id][i, j])

    def latent_value(self, series_id: str, ts: datetime | str) -> float:
        """True value at ts (truth function and error injectors only)."""
        self._get(series_id)
        return self._value(series_id, *self.locate(ts))

    def published_price(self, series_id: str, ts: datetime | str) -> tuple[float, datetime]:
        """(price, price_as_of) a tool would show at ts. ETFs: the latent value at the grid
        time. Mutual funds: the previous trading day's NAV, as of 16:00 that day."""
        s = self._get(series_id)
        i, j = self.locate(ts)
        days = self.calendar.all_days
        if not s.is_mutual_fund:
            return self._value(series_id, i, j), _ts(days[i], self.times[j])
        if i == 0:
            raise ValueError(f"no published NAV before {days[1]}")
        return float(self.closes[series_id][i - 1]), _ts(days[i - 1], self.times[-1])

    def prior_close(self, series_id: str, d: date | str) -> float:
        """Close (NAV for funds) of the trading day before d; d must be in the window."""
        self._get(series_id)
        day = date.fromisoformat(d) if isinstance(d, str) else d
        if day not in self.calendar.days:
            raise ValueError(f"{day} is not a trading day in the price window")
        return float(self.closes[series_id][self.calendar.all_days.index(day) - 1])


def _anchor(x: np.ndarray, anchor_index: int, reference_price: float) -> tuple[np.ndarray, ...]:
    """Prices from a return path x (row 0 unused): close[d] = close[d-1] * (1 + x[d, -1]) and
    path[d, j] = close[d-1] * (1 + x[d, j]), scaled by one constant so close[anchor_index]
    equals reference_price. Returns are unchanged by the scaling."""
    n = x.shape[0]
    close = np.ones(n)
    for d in range(1, n):
        close[d] = close[d - 1] * (1 + x[d, -1])
    path = np.full_like(x, np.nan)
    path[1:] = close[:-1, None] * (1 + x[1:])
    k = reference_price / close[anchor_index]
    return np.round(close * k, DECIMALS), np.round(path * k, DECIMALS)


def pin_fixture(comps: ComponentPaths, spec: ModelSpec, cfg: PriceParams, cal: Calendar) -> None:
    """Script the latent moves at the fixture time (spec section 4). The market factor is pinned
    for the market series; every other pinned series is pinned through its own idio bridge, so
    its path passes exactly through the scripted value. The day's closes are still simulated
    (the second bridge runs from the pinned value to the drawn close)."""
    k = grid_times(cfg).index(cfg.fixture_query_time)
    di = cal.all_days.index(cfg.fixture_date)
    by_id = {s.series_id: s for s in spec.series}
    unknown = set(cfg.fixture_moves) - set(by_id)
    if unknown:
        raise ValueError(f"fixture_moves names unknown instruments {sorted(unknown)}")
    rest_pins: dict[str, float] = {}
    for sid, move in cfg.fixture_moves.items():
        if by_id[sid].loadings == {MARKET: 1.0}:
            comps.pin(MARKET, di, {k: move})
        else:
            rest_pins[sid] = move
    pinned_idio = {idio(sid) for sid in rest_pins}
    for sid, move in rest_pins.items():
        loadings, own = by_id[sid].loadings, idio(sid)
        if own not in loadings:
            raise ValueError(f"{sid} cannot be pinned: it has no idio term of its own")
        others = {c: w for c, w in loadings.items() if c != own}
        if set(others) & pinned_idio:
            raise ValueError(f"{sid} loads on another pinned series' idio term")
        rest = sum(w * float(comps.paths[c][di, k]) for c, w in others.items())
        comps.pin(own, di, {k: (move - rest) / loadings[own]})


def build_prices(
    instruments: pd.DataFrame, cfg: PriceParams, seed: int, fred_dir: Path | None = None
) -> PriceWorld:
    """Deterministic for a given seed, parameters and yield source."""
    cal = build_calendar(cfg, seed)
    daily = simulate_daily(instruments, cfg, seed, cal, fred_dir)
    comps = ComponentPaths(daily, cfg, seed, cal)
    pin_fixture(comps, daily.spec, cfg, cal)
    anchor_index = cal.all_days.index(cfg.anchor_date)
    closes: dict[str, np.ndarray] = {}
    paths: dict[str, np.ndarray] = {}
    for s in daily.spec.series:
        closes[s.series_id], paths[s.series_id] = _anchor(
            comps.series_path(s), anchor_index, s.reference_price
        )
    return PriceWorld(cal, daily.spec.series, grid_times(cfg), closes, paths, daily.yields.source)


def _close_stamp(d: date | str, hhmm: str = "16:00") -> str:
    return f"{d}T{hhmm}:00"


def daily_frame(w: PriceWorld) -> pd.DataFrame:
    days = [d.isoformat() for d in w.calendar.days]
    parts = [
        pd.DataFrame(
            {
                "instrument_id": s.series_id,
                "market_date": days,
                "prior_close": w.closes[s.series_id][:-1],
                "close": w.closes[s.series_id][1:],
                "price_type": "nav" if s.is_mutual_fund else "close",
                "close_as_of": [_close_stamp(d, w.times[-1]) for d in days],
            }
        )
        for s in w.series
    ]
    return pd.concat(parts, ignore_index=True)


def published_frame(w: PriceWorld) -> pd.DataFrame:
    """ETFs publish the latent value at each grid time; funds the previous NAV, as of 16:00 on
    the previous trading day."""
    dates, times = w.grid_keys()
    n_grid = len(w.times)
    prev_close = [_close_stamp(d, w.times[-1]) for d in w.calendar.all_days[:-1]]
    parts = []
    for s in w.series:
        if s.is_mutual_fund:
            price = np.repeat(w.closes[s.series_id][:-1], n_grid)
            as_of = [t for t in prev_close for _ in range(n_grid)]
        else:
            price = np.asarray(w.paths[s.series_id][1:]).ravel()
            as_of = times
        parts.append(
            pd.DataFrame(
                {
                    "instrument_id": s.series_id,
                    "market_date": dates,
                    "time": times,
                    "price": price,
                    "price_as_of": as_of,
                }
            )
        )
    return pd.concat(parts, ignore_index=True)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(SYNTHETIC_HEADER + "\n")
        frame.to_csv(fh, index=False, lineterminator="\n")


def read_price_table(path: Path) -> pd.DataFrame:
    """Read any price-layer CSV. The first line is a '#' synthetic-data notice, so read with
    comment='#'."""
    return pd.read_csv(path, comment="#")


def readme_text(w: PriceWorld, seed: int) -> str:
    cal = w.calendar.frame()
    stress = cal["regime"] == STRESS
    per_split = {k: int(v) for k, v in cal[stress].groupby("split").size().items()}
    return f"""# Synthetic prices

**ALL PRICES IN THIS FOLDER ARE SYNTHETIC.** They are simulated by
`advisor_qa.world.prices` and are not market data. The dates are real 2026 NYSE trading days;
the prices on them are not. Some parameters are calibrated to aggregate statistics (volatility,
beta, correlation) of a provided dataset; none of its prices appear here.

Every CSV starts with a `#` notice line, so read them with `pd.read_csv(path, comment="#")` or
`advisor_qa.world.prices.read_price_table`.

| File | Who may read it |
| --- | --- |
| `{CALENDAR_FILE}` | everyone |
| `{DAILY_FILE}` | everyone |
| `{PUBLISHED_FILE}` | tools and traces |
| `{LATENT_FILE}` | the truth function and error injectors only; never checks or scoring |
| `{EVENTS_FILE}` | empty hook for corporate actions (D4), filled by the error injector |

Mutual funds publish the previous day's NAV (as of 16:00 that day) at every grid time; the latent
value moves intraday. `PROXY-VUG` and `PROXY-MUB` are simulated proxies that are never held.

- seed: {seed} (regime path accepted on seed {w.calendar.regime_seed})
- trading days: {len(cal)} ({cal["market_date"].iloc[0]} to {cal["market_date"].iloc[-1]})
- stress days: {int(stress.sum())} ({stress.mean():.1%}); per split: {per_split}
- yields: {w.yield_source}
"""


def write_prices(w: PriceWorld, out_dir: Path, seed: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(w.calendar.frame(), out_dir / CALENDAR_FILE)
    _write_csv(daily_frame(w), out_dir / DAILY_FILE)
    _write_csv(published_frame(w), out_dir / PUBLISHED_FILE)
    _write_csv(latent_frame(w), out_dir / LATENT_FILE)
    _write_csv(pd.DataFrame(columns=EVENTS_COLUMNS), out_dir / EVENTS_FILE)
    (out_dir / "README.md").write_text(readme_text(w, seed), encoding="utf-8")


def fixture_impact(w: PriceWorld, p: PriceParams) -> float:
    """HH-004217's true impact at the fixture time: sum of qty x (latent - prior close)."""
    ts = _ts(p.fixture_date, p.fixture_query_time)
    return sum(
        qty * (w.latent_value(ins, ts) - w.prior_close(ins, p.fixture_date))
        for _, ins, qty in fx.FIXTURE_HOLDINGS
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "synthetic" / "prices")
    ap.add_argument("--fred-dir", type=Path, default=None, help="folder with DGS2.csv, DGS10.csv")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = ap.parse_args()

    seed, params = load_price_config(args.config)
    seed = args.seed if args.seed is not None else seed
    world = build_prices(load_instruments(), params, seed, args.fred_dir)
    write_prices(world, args.out, seed)

    cal = world.calendar.frame()
    stress = cal["regime"] == STRESS
    log.info("SYNTHETIC prices written to %s (yields: %s)", args.out, world.yield_source)
    log.info("seed %d, regime path from seed %d", seed, world.calendar.regime_seed)
    log.info("stress days %d of %d (%.1f%%)", stress.sum(), len(cal), 100 * stress.mean())
    log.info("stress days per split: %s", cal[stress].groupby("split").size().to_dict())
    impact = fixture_impact(world, params)
    when = f"{params.fixture_date} {params.fixture_query_time}"
    log.info("fixture HH-004217 true impact at %s (latent values): %.2f", when, impact)


if __name__ == "__main__":
    main()
