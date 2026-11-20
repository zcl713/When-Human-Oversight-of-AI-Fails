"""Trading calendar, calm/stress regimes and the train/val/test date split.

The dates are real 2026 NYSE trading days (the fixture is dated 2026-03-10); every price built on
them is synthetic. The split is contiguous blocks with test last, because regimes persist and random
dates would leak volatility information between splits.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from advisor_qa.world.price_params import PriceParams
from advisor_qa.world.streams import stream

log = logging.getLogger(__name__)

CALM, STRESS = "calm", "stress"
TRAIN, VAL, TEST = "train", "val", "test"


def is_trading_day(d: date, holidays: set[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def trading_days(start: date, n: int, holidays: set[date]) -> list[date]:
    """The first n trading days on or after start."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if is_trading_day(d, holidays):
            out.append(d)
        d += timedelta(days=1)
    return out


def previous_trading_day(d: date, holidays: set[date]) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d, holidays):
        d -= timedelta(days=1)
    return d


def assign_split(n: int, val_fraction: float, test_fraction: float) -> list[str]:
    """Contiguous blocks: the last test_fraction of days are test, the val_fraction before them
    validation, the rest train."""
    n_test = round(n * test_fraction)
    n_val = round(n * val_fraction)
    return [TRAIN] * (n - n_val - n_test) + [VAL] * n_val + [TEST] * n_test


def simulate_regimes(
    n_steps: int, p: PriceParams, seed: int, forced_stress: set[int]
) -> np.ndarray:
    """Two-state Markov chain starting calm at index 0. Indices in forced_stress are stress; the
    chain continues from the forced state. Returns a bool array (True = stress)."""
    u = stream(seed, "regime").random(n_steps)
    out = np.zeros(n_steps, dtype=bool)
    for i in range(1, n_steps):
        prev = out[i - 1]
        switch = p.p_stress_to_calm if prev else p.p_calm_to_stress
        out[i] = (not prev) if u[i] < switch else prev
        if i in forced_stress:
            out[i] = True
    return out


def regime_problems(stress: np.ndarray, split: list[str], p: PriceParams) -> list[str]:
    """Why a window regime path is rejected (empty = accepted). stress covers the window only."""
    problems: list[str] = []
    share = float(stress.mean())
    if not p.min_stress_share <= share <= p.max_stress_share:
        problems.append(f"stress share {share:.3f} outside [{p.min_stress_share}, "
                        f"{p.max_stress_share}]")  # fmt: skip
    arr = np.array(split)
    for name in (TRAIN, VAL, TEST):
        k = int(stress[arr == name].sum())
        if k < p.min_stress_days_per_split:
            problems.append(f"{name} has {k} stress days")
    return problems


@dataclass(frozen=True)
class Calendar:
    pre_day: date  # extra day before the window; gives the first day a prior close
    days: list[date]  # the n window days
    stress: np.ndarray  # bool, length n + 1, index 0 = pre_day
    split: list[str]  # length n
    fixture_index: int  # position of the fixture date in `days`
    regime_seed: int  # the seed (seed, seed+1, ...) whose regime path was accepted

    @property
    def all_days(self) -> list[date]:
        return [self.pre_day, *self.days]

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "market_date": [d.isoformat() for d in self.days],
                "regime": np.where(self.stress[1:], STRESS, CALM),
                "split": self.split,
                "is_fixture_day": [i == self.fixture_index for i in range(len(self.days))],
            }
        )


def build_calendar(p: PriceParams, seed: int) -> Calendar:
    """Calendar with a regime path that meets the stress-share and per-split minimums. Tries seed,
    seed+1, ... up to max_regime_tries times."""
    holidays = set(p.holidays)
    days = trading_days(p.start_date, p.n_days, holidays)
    if p.fixture_date not in days:
        raise ValueError(f"fixture date {p.fixture_date} is not in the trading window")
    fixture_index = days.index(p.fixture_date)
    split = assign_split(p.n_days, p.val_date_fraction, p.test_date_fraction)
    problems: list[str] = []
    for attempt in range(p.max_regime_tries):
        s = seed + attempt
        stress = simulate_regimes(p.n_days + 1, p, s, {fixture_index + 1})
        problems = regime_problems(stress[1:], split, p)
        if not problems:
            if attempt:
                log.info("regime path accepted on seed %d (attempt %d)", s, attempt + 1)
            return Calendar(
                previous_trading_day(days[0], holidays), days, stress, split, fixture_index, s
            )
    raise RuntimeError(f"no regime path met the constraints in {p.max_regime_tries} tries: "
                       f"{problems}")  # fmt: skip
