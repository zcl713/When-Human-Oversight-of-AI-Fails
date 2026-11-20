"""Ground-truth portfolio impact, from the latent (true) prices. SYNTHETIC PRICES.

`Truth.impact(household_id, as_of, scope, since=None)` returns the dollar and percent change of the
in-scope holdings (decisions.md, 2026-11-20):

- dollars = sum of qty x (latent value at `as_of` - start value) over holdings in `scope`
- start value = close of the trading day before `since` (or before the day of `as_of` when
  `since` is None, i.e. "today")
- percent = dollars / in-scope value at the start x 100

`scope` is an explicit list of account_ids. Mutual funds use their latent intraday NAV. `as_of`
snaps to the latest grid point at or before it, like `PriceWorld.locate`.

This module reads latent prices, so checks and scoring must never import it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from advisor_qa.world.prices import PriceWorld

WINDOWS = ("intraday", "wtd", "mtd", "ytd")


@dataclass(frozen=True)
class Impact:
    dollars: float  # rounded to cents
    pct: float  # percent, -1.8 means -1.8%
    base: float  # in-scope value at the start (cents)
    current: float  # in-scope value at as_of (cents)
    by_holding: dict[tuple[str, str], float]  # (account_id, instrument_id) -> dollars


def window_start(window: str, as_of: datetime | date, trading_days: Sequence[date]) -> date | None:
    """First trading day of the window containing as_of (None for intraday, i.e. today).
    Windows are clipped to the first day of the price window."""
    if window not in WINDOWS:
        raise ValueError(f"unknown window {window!r}; expected one of {WINDOWS}")
    if window == "intraday":
        return None
    d = as_of.date() if isinstance(as_of, datetime) else as_of
    if window == "wtd":
        lo = d - timedelta(days=d.weekday())
    elif window == "mtd":
        lo = d.replace(day=1)
    else:
        lo = d.replace(month=1, day=1)
    days = [x for x in trading_days if lo <= x <= d]
    if not days:
        raise ValueError(f"no trading day in the {window} window ending {d}")
    return days[0]


class Truth:
    """True impact for any household in the tables, at any grid time in the price window."""

    def __init__(self, holdings: pd.DataFrame, accounts: pd.DataFrame, prices: PriceWorld):
        self.prices = prices
        self._holdings = {
            str(hh): list(
                zip(
                    g["account_id"].astype(str),
                    g["instrument_id"].astype(str),
                    g["qty"].astype(float),
                    strict=True,
                )
            )
            for hh, g in holdings.groupby("household_id", sort=False)
        }
        self._accounts = {
            str(hh): g.reset_index(drop=True) for hh, g in accounts.groupby("household_id")
        }

    def accounts(self, household_id: str) -> pd.DataFrame:
        return self._accounts[household_id]

    def all_accounts(self, household_id: str) -> list[str]:
        return list(self._accounts[household_id]["account_id"])

    def crm_accounts(self, household_id: str) -> list[str]:
        a = self._accounts[household_id]
        return list(a.loc[a["in_crm"].astype(bool), "account_id"])

    def accounts_of_type(self, household_id: str, types: Sequence[str]) -> list[str]:
        a = self._accounts[household_id]
        return list(a.loc[a["type"].isin(list(types)), "account_id"])

    def impact(
        self,
        household_id: str,
        as_of: datetime | str,
        scope: Sequence[str],
        since: date | str | None = None,
    ) -> Impact:
        ts = datetime.fromisoformat(as_of) if isinstance(as_of, str) else as_of
        start_day = (
            ts.date() if since is None else date.fromisoformat(since) if isinstance(since, str)
            else since
        )  # fmt: skip
        if start_day > ts.date():
            raise ValueError(f"since {start_day} is after as_of {ts}")
        wanted = set(scope)
        unknown = wanted - set(self.all_accounts(household_id))
        if unknown:
            raise ValueError(f"accounts {sorted(unknown)} are not in household {household_id}")
        p = self.prices
        base = current = 0.0
        by_holding: dict[tuple[str, str], float] = {}
        for acc, ins, qty in self._holdings.get(household_id, []):
            if acc not in wanted:
                continue
            start = p.prior_close(ins, start_day)
            now = p.latent_value(ins, ts)
            base += qty * start
            current += qty * now
            by_holding[(acc, ins)] = round(qty * (now - start), 2)
        dollars = round(current - base, 2)
        pct = 100 * dollars / base if base else 0.0
        return Impact(dollars, pct, round(base, 2), round(current, 2), by_holding)
