"""Mock tools backed by the synthetic household and price data. SYNTHETIC PRICES.

PROVISIONAL inventory (config `pipeline.tools`) until the real one is confirmed. Every tool is a
pure function of its arguments and the world, so the same seed gives the same outputs and hashes.

Pricing (decisions.md, 2026-11-20): `get_market_prices` returns, per instrument,
- US-listed ETFs: the published intraday price (source `feed`)
- mutual funds: the firm's intraday indicative NAV (source `indicative_nav`), i.e. the true value;
  the official NAV is still the previous day's (what D1 substitutes)
- the money-market fund: its stable $1.00 NAV (source `stable_nav`)
- foreign listings: a fair value (source `fair_value`); the feed's home close is what D6
  substitutes
Indicative and fair values are estimates until the 16:00 strike, so a correct answer says so.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from advisor_qa.world.prices import PriceWorld
from advisor_qa.world.streams import stream

ESTIMATE_SOURCES = {"indicative_nav", "fair_value"}


@dataclass(frozen=True)
class Position:
    account_id: str
    account_type: str
    instrument_id: str
    instrument_type: str
    qty: float


class World:
    """Read access to the tables and prices for the tools, the agent and the injectors."""

    def __init__(
        self,
        households: pd.DataFrame,
        accounts: pd.DataFrame,
        holdings: pd.DataFrame,
        instruments: pd.DataFrame,
        prices: PriceWorld,
        seed: int,
        flow_rate: float,
    ) -> None:
        self.households, self.accounts, self.holdings = households, accounts, holdings
        self.instruments = instruments.set_index("instrument_id")
        self.prices, self.seed, self.flow_rate = prices, seed, flow_rate
        self.series = {s.series_id: s for s in prices.series}
        self._acc = {str(h): g for h, g in accounts.groupby("household_id")}
        self._pos: dict[str, list[Position]] = {}
        types = dict(zip(accounts["account_id"], accounts["type"], strict=True))
        itype = self.instruments["instrument_type"]
        for h, g in holdings.groupby("household_id"):
            self._pos[str(h)] = [
                Position(a, str(types[a]), i, str(itype[i]), float(q))
                for a, i, q in zip(g["account_id"], g["instrument_id"], g["qty"], strict=True)
            ]

    def account_table(self, household_id: str) -> pd.DataFrame:
        return self._acc[household_id]

    def account_id(self, household_id: str, account_type: str) -> str:
        a = self._acc[household_id]
        return str(a.loc[a["type"] == account_type, "account_id"].iloc[0])

    def positions(self, household_id: str) -> list[Position]:
        return list(self._pos.get(household_id, []))

    def ticker(self, instrument_id: str) -> str:
        return str(self.instruments.loc[instrument_id, "ticker"])

    def kind(self, instrument_id: str) -> str:
        """etf, fund, money_market or foreign: how the instrument is priced."""
        s = self.series[instrument_id]
        if s.foreign:
            return "foreign"
        if s.is_mutual_fund:
            mmf = self.instruments.loc[instrument_id, "instrument_type"] == "money_market_fund"
            return "money_market" if mmf else "fund"
        return "etf"

    def flow_today(self, household_id: str, d: date) -> tuple[str, float] | None:
        """Same-day deposit (+) or withdrawal (-) into a non-held-away account's cash (C3)."""
        rng = stream(self.seed, "cash_flow", household_id, d)
        hit, sign, amount = rng.random(), rng.random(), rng.uniform(2_000, 50_000)
        a = self._acc[household_id]
        own = a[~a["held_away"].astype(bool)]
        if hit >= self.flow_rate or own.empty:
            return None
        acc = str(own["account_id"].iloc[int(rng.integers(len(own)))])
        return acc, round(amount if sign < 0.5 else -amount, 2)


def _ts(x: datetime) -> str:
    return x.isoformat()


class Tools:
    """One tool environment per run, at the run's timestamp."""

    def __init__(self, world: World, as_of: datetime) -> None:
        self.w, self.as_of = world, as_of

    def get_client_accounts(self, household_id: str) -> dict[str, Any]:
        """Accounts in the CRM, with their value at the prior close (pre-flow, as booked)."""
        a = self.w.account_table(household_id)
        d = self.as_of.date()
        flow = self.w.flow_today(household_id, d)
        out = []
        for r in a.itertuples(index=False):
            if not bool(r.in_crm):
                continue
            value = sum(
                p.qty * self.w.prices.prior_close(p.instrument_id, d)
                for p in self.w.positions(household_id)
                if p.account_id == r.account_id
            )
            if flow and flow[0] == r.account_id:
                value -= flow[1]
            out.append(
                {
                    "account_id": str(r.account_id),
                    "type": str(r.type),
                    "held_away": bool(r.held_away),
                    "crm_value": round(value, 2),
                }
            )
        return {"household_id": household_id, "accounts": out}

    def get_positions(self, household_id: str) -> dict[str, Any]:
        d = self.as_of.date()
        flow = self.w.flow_today(household_id, d)
        types = sorted(set(self.w.account_table(household_id)["type"]))
        return {
            "snapshot_id": f"snap-{d.isoformat()}-{self.as_of:%H%M}",
            "accounts_in_feed": types,
            "positions": [
                {
                    "account_id": p.account_id,
                    "account_type": p.account_type,
                    "instrument_id": p.instrument_id,
                    "instrument_type": p.instrument_type,
                    "qty": p.qty,
                }
                for p in self.w.positions(household_id)
            ],
            "cash_flows_today": (
                [{"account_id": flow[0], "amount": flow[1]}] if flow is not None else []
            ),
        }

    def price(self, instrument_id: str, start_day: date) -> dict[str, Any]:
        p, kind = self.w.prices, self.w.kind(instrument_id)
        start = p.prior_close(instrument_id, start_day)
        if kind == "etf":
            px, as_of = p.published_price(instrument_id, self.as_of)
            return {"price": px, "as_of": _ts(as_of), "source": "feed", "start_price": start}
        i, j = p.locate(self.as_of)
        snapped = datetime.fromisoformat(f"{p.calendar.all_days[i]}T{p.times[j]}:00")
        source = {"fund": "indicative_nav", "money_market": "stable_nav", "foreign": "fair_value"}
        return {
            "price": p.latent_value(instrument_id, self.as_of),
            "as_of": _ts(snapped),
            "source": source[kind],
            "start_price": start,
        }

    def get_market_prices(self, instrument_ids: Sequence[str], start_day: date) -> dict[str, Any]:
        return {
            "as_of": _ts(self.as_of),
            "prices": {i: self.price(i, start_day) for i in sorted(set(instrument_ids))},
        }

    def get_benchmark(self, ticker: str, start_day: date) -> dict[str, Any]:
        ins = str(self.w.instruments.index[self.w.instruments["ticker"] == ticker][0])
        px, as_of = self.w.prices.published_price(ins, self.as_of)
        start = self.w.prices.prior_close(ins, start_day)
        return {
            "index": ticker,
            "as_of": _ts(as_of),
            "return_pct": round(100 * (px / start - 1), 4),
        }

    def get_market_news(self) -> dict[str, Any]:
        d = self.as_of.date()
        i = self.w.prices.calendar.all_days.index(d)
        stressed = bool(self.w.prices.calendar.stress[i])
        heads = (
            ["Volatility jumps as stocks swing sharply", "Investors move toward Treasuries"]
            if stressed
            else ["Stocks trade in a narrow range", "Bond yields little changed"]
        )
        return {"date": d.isoformat(), "headlines": heads}

    def get_client_notes(self, household_id: str) -> dict[str, Any]:
        rng = stream(self.w.seed, "client_notes", household_id)
        pool = [
            "Prefers a phone call before any trade.",
            "Planning to retire within ten years.",
            "Asked to keep cash for a home renovation.",
            "Tax documents are handled by an outside accountant.",
            "Comfortable with market swings; long horizon.",
        ]
        picks = sorted(rng.choice(len(pool), size=2, replace=False))
        return {"household_id": household_id, "notes": [pool[int(k)] for k in picks]}


def calculate_portfolio_impact(
    holdings: Sequence[dict[str, Any]],
    net_flows_today: float = 0.0,
    benchmark_pct: float | None = None,
) -> dict[str, Any]:
    """The calculation tool: impact from qty, price and start price per holding. Same-day flows
    are excluded (the positions are post-flow, so qty x price change already ignores them)."""
    by_holding: dict[str, float] = {}
    base = current = 0.0
    for h in holdings:
        key = f"{h['account_id']}/{h['instrument_id']}"
        base += h["qty"] * h["start_price"]
        current += h["qty"] * h["price"]
        by_holding[key] = round(h["qty"] * (h["price"] - h["start_price"]), 2)
    dollars = round(current - base, 2)
    pct = round(100 * dollars / base, 4) if base else 0.0
    out: dict[str, Any] = {
        "dollar_impact": dollars,
        "pct_impact": pct,
        "base": round(base, 2),
        "current_value": round(current, 2),
        "by_holding": by_holding,
        "net_flows_today": round(net_flows_today, 2),
        "flows_excluded": True,
    }
    if benchmark_pct is not None:
        out["benchmark_pct"] = benchmark_pct
        out["relative_pct"] = round(pct - benchmark_pct, 4)
    return out
