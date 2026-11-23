"""Mock tools backed by the synthetic household and price data. SYNTHETIC PRICES.

The inventory is ours (the client workbook has none; decisions.md 2026-11-23). It is modelled on a
typical advisor platform and is listed in config `pipeline.tools`. Every tool is a pure function
of its arguments and the world, so the same seed gives the same outputs and hashes.

Pricing follows the client workbook (D1: funds are "priced once daily"; O6: "intraday value is
modeled, not observed"):
- US-listed ETFs: `get_quotes` returns the live price.
- Mutual funds: `get_fund_navs` returns the official NAV, which is yesterday's close, plus an
  intraday *estimate* from a proxy (the fund's proxy ETF, or for target-date funds their published
  equity/bond mix). The estimate is not the true value; the gap is tracking error.
- The money-market fund: a stable $1.00 NAV.
- The foreign holding: `get_quotes` returns the home-market close (market closed, FX at the last
  close) plus a fair-value estimate from the US market move.
A correct answer uses the estimates, says they are estimates, and names the method.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from advisor_qa.world.price_params import PriceParams
from advisor_qa.world.prices import PriceWorld
from advisor_qa.world.streams import stream

ESTIMATE_SOURCES = {"nav_estimate", "fair_value"}
HISTORY_STARTS = date(2019, 1, 2)  # transaction history before this is missing (O4 setup)

# instrument_id -> [(proxy series id, weight)]; the estimate moves by sum(weight x proxy return)
EstimateRules = dict[str, list[tuple[str, float]]]


def estimate_rules(instruments: pd.DataFrame, p: PriceParams) -> EstimateRules:
    """How the platform estimates what it cannot observe intraday. Uses only public facts: each
    fund's proxy ETF, target-date funds' published equity weight, and an assumed beta of the
    foreign holding to the US market."""
    inst = instruments.set_index("instrument_id")
    by_ticker = {str(t): str(i) for i, t in inst["ticker"].items()}
    spy, bnd = by_ticker[p.market_ticker], by_ticker[p.target_date_bond_ticker]
    out: EstimateRules = {}
    for ins, r in inst.iterrows():
        ins = str(ins)
        if r["pricing"] == "foreign_close":
            out[ins] = [(spy, p.betas[str(r["ticker"])])]
        elif not bool(r["is_mutual_fund"]):
            continue
        elif r["instrument_type"] == "money_market_fund":
            out[ins] = []
        elif r["instrument_type"] == "target_date_fund":
            w = p.target_date_equity_weight[str(r["ticker"])]
            out[ins] = [(spy, w), (bnd, 1 - w)]
        else:
            proxy = str(r["proxy_ticker"])
            out[ins] = [(by_ticker.get(proxy, f"PROXY-{proxy}"), 1.0)]
    return out


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
        rules: EstimateRules,
        directory: Any = None,  # pipeline.directory.Directory (set by the generator)
    ) -> None:
        self.households, self.accounts, self.holdings = households, accounts, holdings
        self.instruments = instruments.set_index("instrument_id")
        self.prices, self.seed, self.flow_rate, self.rules = prices, seed, flow_rate, rules
        self.directory = directory
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
        if instrument_id in self.instruments.index:
            return str(self.instruments.loc[instrument_id, "ticker"])
        return instrument_id.removeprefix("PROXY-")

    def duration(self, instrument_id: str) -> float:
        d = pd.to_numeric(self.instruments["duration_years"], errors="coerce").fillna(0.0)
        return float(d.to_dict()[instrument_id])

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

    def estimate(self, instrument_id: str, as_of: datetime, official: float) -> float:
        """The platform's intraday estimate: official price x (1 + weighted proxy return today)."""
        p, d = self.prices, as_of.date()
        move = sum(
            w * (p.published_price(s, as_of)[0] / p.prior_close(s, d) - 1)
            for s, w in self.rules[instrument_id]
        )
        return round(official * (1 + move), 6)

    def method(self, instrument_id: str) -> str:
        rule = self.rules[instrument_id]
        if self.kind(instrument_id) == "foreign":
            return f"home close x (1 + {rule[0][1]:.2f} x {self.ticker(rule[0][0])} move today)"
        if not rule:
            return "stable $1.00 NAV"
        if len(rule) == 1:
            return f"proxy {self.ticker(rule[0][0])}"
        return " + ".join(f"{w:.2f} x {self.ticker(s)}" for s, w in rule)


def _ts(x: datetime) -> str:
    return x.isoformat()


class Tools:
    """One tool environment per run, at the run's timestamp."""

    def __init__(self, world: World, as_of: datetime) -> None:
        self.w, self.as_of = world, as_of

    # --- clients and accounts ---------------------------------------------------------------------

    def search_clients(self, name: str) -> dict[str, Any]:
        d = self.w.directory
        matches = [
            {"household_id": h, "client_name": n, "advisor_id": d.advisor_of[h]}
            for h, n in sorted(d.name_of.items())
            if n.split()[-1] == name
        ]
        return {"query": name, "matches": matches}

    def get_client_profile(self, household_id: str) -> dict[str, Any]:
        rng = stream(self.w.seed, "client_profile", household_id)
        pool = [
            "Prefers a phone call before any trade.",
            "Planning to retire within ten years.",
            "Asked to keep cash for a home renovation.",
            "Tax documents are handled by an outside accountant.",
            "Comfortable with market swings; long horizon.",
        ]
        picks = sorted(rng.choice(len(pool), size=2, replace=False))
        risk = ["conservative", "moderate", "growth"][int(rng.integers(3))]
        return {
            "household_id": household_id,
            "risk_tolerance": risk,
            "client_since": f"{int(rng.integers(2008, 2019))}-01-01",
            "notes": [pool[int(k)] for k in picks],
        }

    def get_accounts(self, household_id: str) -> dict[str, Any]:
        """Accounts in the CRM, with their booked value at the prior close (before any flow)."""
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
                    "booked_value": round(value, 2),
                }
            )
        return {"household_id": household_id, "accounts": out}

    def _positions(
        self, household_id: str, account_ids: Sequence[str], snap: str
    ) -> dict[str, Any]:
        types = sorted(set(self.w.account_table(household_id)["type"]))
        wanted = set(account_ids)
        return {
            "snapshot_id": snap,
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
                if p.account_id in wanted
            ],
        }

    def get_positions(self, household_id: str, account_ids: Sequence[str]) -> dict[str, Any]:
        """Current positions from the custodian feed."""
        snap = f"snap-{self.as_of.date().isoformat()}-{self.as_of:%H%M}"
        return self._positions(household_id, account_ids, snap)

    def get_eod_positions(self, household_id: str, account_ids: Sequence[str]) -> dict[str, Any]:
        """Last night's end-of-day snapshot (a plausible wrong choice for an intraday question)."""
        days = self.w.prices.calendar.all_days
        prev = days[days.index(self.as_of.date()) - 1]
        return self._positions(household_id, account_ids, f"snap-{prev.isoformat()}-1600")

    def get_transactions(
        self, household_id: str, account_ids: Sequence[str], since: date
    ) -> dict[str, Any]:
        flow = self.w.flow_today(household_id, self.as_of.date())
        tx = []
        if flow is not None and flow[0] in set(account_ids):
            kind = "deposit" if flow[1] > 0 else "withdrawal"
            tx.append({"account_id": flow[0], "type": kind, "amount": flow[1],
                       "date": self.as_of.date().isoformat()})  # fmt: skip
        return {"since": since.isoformat(), "transactions": tx}

    # --- prices -------------------------------------------------------------------------------

    def get_quotes(self, instrument_ids: Sequence[str], start_day: date) -> dict[str, Any]:
        """ETFs: live price. Foreign listings: home close (market closed) and a fair value."""
        p, out = self.w.prices, {}
        for ins in sorted(set(instrument_ids)):
            px, as_of = p.published_price(ins, self.as_of)
            q: dict[str, Any] = {"price": px, "as_of": _ts(as_of),
                                 "start_price": p.prior_close(ins, start_day)}  # fmt: skip
            if self.w.kind(ins) == "etf":  # market-data vendor's price: within a few bp normally
                noise = stream(self.w.seed, "vendor_price", ins, _ts(self.as_of)).normal(0, 2e-4)
                q["vendor_price"] = round(px * (1 + float(noise)), 6)
            if self.w.kind(ins) == "foreign":
                q.update(
                    market_status="closed",
                    fx_rate_as_of=_ts(as_of),
                    fair_value_estimate=self.w.estimate(ins, self.as_of, px),
                    fair_value_method=self.w.method(ins),
                )
            out[ins] = q
        return {"as_of": _ts(self.as_of), "quotes": out}

    def get_fund_navs(self, instrument_ids: Sequence[str], start_day: date) -> dict[str, Any]:
        """Official NAV (yesterday's close) and the intraday estimate with its method."""
        p, out = self.w.prices, {}
        for ins in sorted(set(instrument_ids)):
            nav, nav_as_of = p.published_price(ins, self.as_of)
            out[ins] = {
                "nav": nav,
                "nav_as_of": _ts(nav_as_of),
                "estimate": self.w.estimate(ins, self.as_of, nav),
                "estimate_as_of": _ts(self._snapped()),
                "estimate_method": self.w.method(ins),
                "start_price": p.prior_close(ins, start_day),
            }
        return {"as_of": _ts(self.as_of), "funds": out}

    def _snapped(self) -> datetime:
        p = self.w.prices
        i, j = p.locate(self.as_of)
        return datetime.fromisoformat(f"{p.calendar.all_days[i]}T{p.times[j]}:00")

    def get_corporate_actions(self, instrument_ids: Sequence[str]) -> dict[str, Any]:
        return {"date": self.as_of.date().isoformat(), "actions": []}

    # --- performance and context --------------------------------------------------------------

    def get_inception_performance(self, household_id: str) -> dict[str, Any]:
        since = self.get_client_profile(household_id)["client_since"]
        return {"period": "inception", "client_since": since, "status": "incomplete",
                "history_starts": HISTORY_STARTS.isoformat(), "return_pct": None}  # fmt: skip

    def get_benchmark_returns(self, index: str, start_day: date) -> dict[str, Any]:
        """index is a ticker ('SPY') or a blend ('SPY60/BND40')."""
        parts = [(index, 1.0)] if "/" not in index else [
            (x.rstrip("0123456789"), int(x[len(x.rstrip("0123456789")):]) / 100)
            for x in index.split("/")
        ]  # fmt: skip
        inst = self.w.instruments
        ret = 0.0
        for ticker, w in parts:
            ins = str(inst.index[inst["ticker"] == ticker][0])
            px, _ = self.w.prices.published_price(ins, self.as_of)
            ret += w * (px / self.w.prices.prior_close(ins, start_day) - 1)
        return {"index": index, "as_of": _ts(self.as_of), "return_pct": round(100 * ret, 4)}

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

    def get_treasury_yields(self) -> dict[str, Any]:
        """The previous trading day's change in the 10-year Treasury yield."""
        days = self.w.prices.calendar.all_days
        i = days.index(self.as_of.date())
        change = self.w.prices.yield_change_10y[i - 1] if i >= 2 else 0.0
        bps = round(change * 1e4, 1)
        return {"date": days[i - 1].isoformat(), "ust10y_change_bps": bps,
                "ust10y_change_pct": round(bps / 100, 4)}  # fmt: skip

    def draft_client_email(self, household_id: str, body: str) -> dict[str, Any]:
        """Write action: drafts an email for the advisor to review (not used by a correct run)."""
        return {"household_id": household_id, "status": "drafted", "chars": len(body)}


def calculate_portfolio_impact(
    holdings: Sequence[dict[str, Any]],
    net_flows_today: float = 0.0,
    benchmark_pct: float | None = None,
    base_override: float | None = None,
) -> dict[str, Any]:
    """Impact from qty, price and start price per holding. Used by `calculate_portfolio_impact`
    (today) and `get_performance` (week, month, year). Flows are excluded: the positions are
    post-flow, so qty x price change already ignores them."""
    by_holding: dict[str, float] = {}
    base = current = 0.0
    for h in holdings:
        key = f"{h['account_id']}/{h['instrument_id']}"
        base += h["qty"] * h["start_price"]
        current += h["qty"] * h["price"]
        by_holding[key] = round(h["qty"] * (h["price"] - h["start_price"]), 2)
    dollars = round(current - base, 2)
    if base_override is not None:  # the caller supplied a base (e.g. booked account totals)
        base = base_override
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
