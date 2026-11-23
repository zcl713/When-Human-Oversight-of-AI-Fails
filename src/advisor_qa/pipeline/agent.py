"""The correct agent: resolves the question, calls the mock tools, answers from their outputs.

Error injection never edits a finished trace. An injector configures `Faults` (hooks on intent
resolution, tool outputs, how prices are chosen, the calculation, the answer, and extra tool
calls) and the same agent runs with them, so every trace stays internally consistent: hashes match
outputs, numbers match the calculation that produced them, and the answer text matches the derived
Answer fields.

What "correct" means follows the client workbook's pass criteria: estimate funds and closed
foreign markets and say how (D1, D6, O6), state coverage (D2), restate the interpretation (I1,
I2, I5), pick a benchmark that fits the client's mix (I4), give facts but no advice (I3, O2, O3,
O5), convert basis points correctly (C7), and say what cannot be verified (O4).
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np

from advisor_qa import derive
from advisor_qa.otel.emit import RunRecord, ToolRecord, build_trace
from advisor_qa.otel.model import TracesData
from advisor_qa.pipeline.directory import Directory, surname
from advisor_qa.pipeline.params import PipelineParams
from advisor_qa.pipeline.questions import ACCOUNT_LABEL, AMBIGUOUS_KINDS, MIX, Question
from advisor_qa.pipeline.tools import (
    ESTIMATE_SOURCES,
    HISTORY_STARTS,
    Tools,
    World,
    calculate_portfolio_impact,
)
from advisor_qa.qa.view import derive_view
from advisor_qa.schema.trace import RunTrace
from advisor_qa.truth.impact import window_start
from advisor_qa.world.streams import stream

WINDOW_WORD = {"intraday": "today", "wtd": "this week", "mtd": "this month", "ytd": "this year"}
BENCH_NAME = {"SPY": "the S&P 500 (SPY)", "VTI": "the total market (VTI)", "BND": "US bonds (BND)"}
LATENCY_MS = {
    "search_clients": 90,
    "get_client_profile": 110,
    "get_accounts": 120,
    "get_positions": 210,
    "get_eod_positions": 150,
    "get_transactions": 140,
    "get_corporate_actions": 80,
    "get_quotes": 180,
    "get_fund_navs": 220,
    "calculate_portfolio_impact": 45,
    "get_performance": 260,
    "get_benchmark_returns": 150,
    "get_market_news": 260,
    "get_treasury_yields": 120,
}
ALTERNATIVE_OF = {
    "portfolio_impact": "portfolio_performance_ytd",
    "benchmark_comparison": "portfolio_impact",
    "market_concern": "general_reassurance",
    "sell_inquiry": "trade_request",
    "tax_inquiry": "tax_advice",
    "rates_impact": "rate_forecast",
    "portfolio_performance": "portfolio_impact",
}
FUND_KINDS = {"fund", "money_market"}


@dataclass
class Resolution:
    household_id: str
    intent: str
    scope_types: list[str]
    window: str
    benchmark: str | None
    restate: bool
    alternatives: list[tuple[str, float]]


@dataclass
class Calc:
    output: dict[str, Any]
    tool_name: str = "calculate_portfolio_impact"  # or get_performance
    computed_by: str = "tool"  # "tool" or "llm"
    via_tool: bool = True  # False: the agent did the arithmetic itself (no calc tool call)
    intermediate: dict[str, Any] = field(default_factory=dict)
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunState:
    q: Question
    world: World
    tools: Tools
    rng: np.random.Generator
    res: Resolution | None = None
    start_day: date | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)  # holdings used
    flows: float = 0.0
    calc: Calc | None = None
    yields: dict[str, Any] | None = None
    benchmark: str | None = None  # index actually used
    accounts: list[dict[str, Any]] = field(default_factory=list)  # get_accounts result
    not_in_feed: list[str] = field(default_factory=list)  # account types the feed lacks
    timed_out: list[str] = field(default_factory=list)  # account types whose positions failed
    discrepancies: list[str] = field(default_factory=list)  # tickers: custodian vs vendor > 1%


Sentence = list[tuple[str, str]]  # (tag, text), in order
ResolveHook = Callable[[Resolution, RunState], None]
ToolHook = Callable[[str, dict[str, Any], RunState], str | None]  # mutate output; status
RowHook = Callable[[dict[str, Any], dict[str, Any], RunState], None]  # (row, quote, state)
CalcHook = Callable[[Calc, RunState], None]
AnswerHook = Callable[[Sentence, RunState], None]
ExtraCall = Callable[[RunState], tuple[str, dict[str, Any], dict[str, Any]] | None]


@dataclass
class Faults:
    resolve: list[ResolveHook] = field(default_factory=list)
    crm_drop_types: set[str] = field(default_factory=set)  # accounts missing from the CRM
    tool: list[ToolHook] = field(default_factory=list)
    row: list[RowHook] = field(default_factory=list)  # how the agent picks a holding's price
    calc: list[CalcHook] = field(default_factory=list)
    answer: list[AnswerHook] = field(default_factory=list)
    extra_calls: list[ExtraCall] = field(default_factory=list)


@dataclass(frozen=True)
class RunResult:
    trace: RunTrace  # the QA view, derived from `otel` only
    otel: TracesData  # what the instrumented agent emitted
    answer: str
    claimed_dollars: float | None  # first dollar figure in the answer
    resolution: Resolution


def _clock(ts: datetime) -> str:
    h = ts.hour % 12 or 12
    return f"{h}:{ts.minute:02d} {'AM' if ts.hour < 12 else 'PM'}"


def _join(labels: list[str]) -> str:
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def _day(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def recompute(calc: Calc, rows: list[dict[str, Any]], flows: float) -> None:
    """Redo the calculation tool's output from rows (after a fault changed prices or starts)."""
    bench = calc.output.get("benchmark_pct")
    calc.output = calculate_portfolio_impact(rows, flows, bench, calc.args.get("base"))


def price_row(position: dict[str, Any], quote: dict[str, Any], kind: str) -> dict[str, Any]:
    """The correct choice of price for one holding, from its quote or NAV record."""
    row = {**position, "start_price": quote["start_price"], "kind": kind}
    if kind == "etf":
        row.update(price=quote["price"], as_of=quote["as_of"], source="live", tool="get_quotes")
    elif kind == "foreign":
        row.update(price=quote["fair_value_estimate"], as_of=quote["as_of_now"],
                   source="fair_value", method=quote["fair_value_method"],
                   tool="get_quotes")  # fmt: skip
    elif kind == "money_market":
        row.update(price=quote["nav"], as_of=quote["estimate_as_of"], source="stable_nav",
                   tool="get_fund_navs")  # fmt: skip
    else:
        row.update(price=quote["estimate"], as_of=quote["estimate_as_of"], source="nav_estimate",
                   method=quote["estimate_method"], tool="get_fund_navs")  # fmt: skip
    return row


def holding_args(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The holdings the agent passes to the calculation tool (what it priced, and how)."""
    return [
        {"account_id": r["account_id"], "account_type": r["account_type"],
         "instrument_id": r["instrument_id"], "instrument_type": r["instrument_type"],
         "qty": r["qty"], "price": r["price"], "price_as_of": r["as_of"],
         "price_source": r["source"], "start_price": r["start_price"]}
        for r in rows
    ]  # fmt: skip


class Agent:
    def __init__(self, world: World, directory: Directory, params: PipelineParams) -> None:
        self.w, self.dir, self.p = world, directory, params

    # --- intent ---------------------------------------------------------------------------------

    def resolve(self, q: Question, rng: np.random.Generator) -> Resolution:
        ambiguous = q.kind in AMBIGUOUS_KINDS
        top = float(rng.uniform(0.5, 0.65) if ambiguous else rng.uniform(0.85, 0.97))
        second = float(rng.uniform(0.25, top - 0.1) if ambiguous else rng.uniform(0.01, 0.08))
        alts = [(q.intent, round(top, 2)), (ALTERNATIVE_OF[q.intent], round(second, 2))]
        return Resolution(
            q.household_id, q.intent, list(q.scope_types), q.window, q.benchmark, True, alts
        )

    def calc_tool(self, window: str) -> str:
        return self.p.calc_tools["intraday" if window == "intraday" else "period"]

    # --- run ------------------------------------------------------------------------------------

    def run(self, q: Question, faults: Faults | None = None) -> RunResult:
        f = faults or Faults()
        st = RunState(q, self.w, Tools(self.w, q.timestamp), stream(self.w.seed, "agent", q.run_id))
        res = self.resolve(q, st.rng)
        for hook in f.resolve:
            hook(res, st)
        st.res = res
        records: list[ToolRecord] = []
        step = 1

        def call(name: str, args: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
            status = "ok"
            for hook in f.tool:
                status = hook(name, out, st) or status
            latency = round(LATENCY_MS.get(name, 200) * float(np.exp(st.rng.normal(0, 0.25))), 1)
            message = None
            if status == "timeout":
                latency = round(latency + 30_000, 1)
                message = "upstream timeout on part of the request"
            records.append(ToolRecord(step, name, args, out, status, message, latency))
            return out

        hh, ts, t = res.household_id, q.timestamp, st.tools
        crm_types: list[str] = []
        calc: Calc | None = None
        news: dict[str, Any] | None = None
        inception: dict[str, Any] | None = None
        if res.intent != "general_reassurance":
            if q.names_client:
                name = surname(self.dir.name_of[q.household_id])
                call("search_clients", {"name": name}, t.search_clients(name))
            call("get_client_profile", {"household_id": hh}, t.get_client_profile(hh))
            acc_out = call("get_accounts", {"household_id": hh}, t.get_accounts(hh))
            st.accounts = acc_out["accounts"]
            crm_types = [a["type"] for a in st.accounts]
            res.scope_types = [x for x in res.scope_types if x in crm_types]
            ids_in_scope = [a["account_id"] for a in st.accounts if a["type"] in res.scope_types]
            step = 2
            pos_args = {"household_id": hh, "account_ids": ids_in_scope}
            pos_out = call("get_positions", pos_args, t.get_positions(hh, ids_in_scope))
            positions = pos_out["positions"]
            st.not_in_feed = [x for x in res.scope_types if x not in pos_out["accounts_in_feed"]]
            failed = {e["account_id"] for e in pos_out.get("errors", [])}
            st.timed_out = [a["type"] for a in st.accounts if a["account_id"] in failed]
            res.scope_types = [x for x in res.scope_types if x not in st.not_in_feed]
            st.start_day = window_start(res.window, ts, self.w.prices.calendar.days) or ts.date()
            tx = call("get_transactions", {**pos_args, "since": ts.date().isoformat()},
                      t.get_transactions(hh, ids_in_scope, ts.date()))  # fmt: skip
            st.flows = round(sum(x["amount"] for x in tx["transactions"]), 2)
            step = 3
            ids = sorted({p["instrument_id"] for p in positions})
            call("get_corporate_actions", {"instrument_ids": ids}, t.get_corporate_actions(ids))
            quoted = [i for i in ids if self.w.kind(i) not in FUND_KINDS]
            funds = [i for i in ids if self.w.kind(i) in FUND_KINDS]
            px_args: dict[str, Any] = {"as_of": ts.isoformat()}
            if res.window != "intraday":
                px_args["since"] = st.start_day.isoformat()
            quotes: dict[str, dict[str, Any]] = {}
            if quoted:
                out = call("get_quotes", {**px_args, "instrument_ids": quoted},
                           t.get_quotes(quoted, st.start_day))  # fmt: skip
                quotes.update(out["quotes"])
                st.discrepancies = sorted(
                    self.w.ticker(i)
                    for i, x in out["quotes"].items()
                    if "vendor_price" in x and abs(x["vendor_price"] / x["price"] - 1) > 0.01
                )
            if funds:
                out = call("get_fund_navs", {**px_args, "instrument_ids": funds},
                           t.get_fund_navs(funds, st.start_day))  # fmt: skip
                quotes.update(out["funds"])
            for p in positions:
                quote = quotes.get(p["instrument_id"])
                if quote is None:
                    continue
                quote = {**quote, "as_of_now": t._snapped().isoformat()}
                row = price_row(p, quote, self.w.kind(p["instrument_id"]))
                for rhook in f.row:
                    rhook(row, quote, st)
                st.rows.append(row)
            bench_pct = None
            if res.intent == "benchmark_comparison":
                st.benchmark = self.benchmark_for(res.benchmark, st.rows)
                b_args = {"index": st.benchmark, "as_of": ts.isoformat()}
                bench_pct = call("get_benchmark_returns", b_args,
                                 t.get_benchmark_returns(st.benchmark, st.start_day)
                                 )["return_pct"]  # fmt: skip
            if res.intent == "rates_impact":
                st.yields = call("get_treasury_yields", {"date": ts.date().isoformat()},
                                 t.get_treasury_yields())  # fmt: skip
            if res.intent == "portfolio_performance":
                inception = call("get_performance", {"household_id": hh, "period": "inception"},
                                 t.get_inception_performance(hh))  # fmt: skip
            if res.intent == "market_concern":
                news = call("get_market_news", {"date": ts.date().isoformat()},
                            t.get_market_news())  # fmt: skip
            step = 4
            calc = Calc(calculate_portfolio_impact(st.rows, st.flows, bench_pct),
                        tool_name=self.calc_tool(res.window))  # fmt: skip
            calc.args = {"period": res.window, "as_of": ts.isoformat(),
                         "net_flows_today": st.flows}  # fmt: skip
            if bench_pct is not None:
                calc.args["benchmark_pct"] = bench_pct
            for chook in f.calc:
                chook(calc, st)
            if calc.via_tool:
                call(calc.tool_name, {**calc.args, "holdings": holding_args(st.rows)}, calc.output)
        else:
            news = call("get_market_news", {"date": ts.date().isoformat()}, t.get_market_news())
        for extra in f.extra_calls:
            got = extra(st)
            if got is not None:
                call(*got)

        st.calc = calc
        sentences = self.compose(q, res, st, calc, news, inception, crm_types)
        for ahook in f.answer:
            ahook(sentences, st)
        answer = " ".join(s for _, s in sentences if s)
        conv = "conv_" + hashlib.sha256(f"{q.advisor_id}|{q.household_id}|{ts.date()}".encode()
                                        ).hexdigest()[:24]  # fmt: skip
        rec = RunRecord(q.run_id, ts, q.advisor_id, hh, conv, q.turn, q.prompt, answer,
                        res.intent, res.alternatives, list(res.scope_types), res.window,
                        records)  # fmt: skip
        otel = build_trace(rec, stream(self.w.seed, "otel", q.run_id))
        dollars = [v for _, v, k in derive.parse_numbers(answer) if k == "dollars"]
        return RunResult(derive_view(otel, self.p), otel, answer,
                         dollars[0] if dollars else None, res)  # fmt: skip

    def benchmark_for(self, asked: str | None, rows: list[dict[str, Any]]) -> str:
        if asked and asked != MIX:
            return asked
        share = derive.equity_share((r["instrument_type"], r["qty"] * r["start_price"])
                                    for r in rows)  # fmt: skip
        return derive.mix_benchmark(share)

    # --- answer ---------------------------------------------------------------------------------

    def compose(
        self,
        q: Question,
        res: Resolution,
        st: RunState,
        calc: Calc | None,
        news: dict[str, Any] | None,
        inception: dict[str, Any] | None,
        crm_types: list[str],
    ) -> Sentence:
        name = self.dir.name_of[res.household_id]
        out: Sentence = []
        if calc is None:  # general reassurance: no numbers
            out.append(("main", "No need to worry: this is normal market volatility and it "
                                "will be fine."))  # fmt: skip
            if news:
                out.append(("context", f"Today's news: {news['headlines'][0].lower()}."))
            out.append(("disclosure", derive.DISCLOSURE))
            return out
        labels = [ACCOUNT_LABEL[x] for x in res.scope_types]
        everything = set(res.scope_types) == set(crm_types)
        scope_desc = f"all accounts ({_join(labels)})" if everything else f"the {_join(labels)}"
        since = (
            "since yesterday's close" if res.window == "intraday"
            else f"since the close before {st.start_day:%b} {st.start_day.day}"
            if st.start_day else ""
        )  # fmt: skip
        if res.restate:
            what = f"the change {since}"
            if inception is not None:
                what = "the return since the client started"
            out.append(("restate", f"{derive.RESTATE_MARK} {what} across {scope_desc}."))
        if inception is not None:
            out.append(("caveat", "I can't verify a since-inception return: transactions before "
                                  f"{_day(HISTORY_STARTS)} are missing, so here is this year's "
                                  "change instead."))  # fmt: skip
        o = calc.output
        out.append(("main", f"As of {_clock(q.timestamp)} ET, {name}'s household has changed by "
                            f"{derive.fmt_dollars(o['dollar_impact'])} "
                            f"({derive.fmt_pct(o['pct_impact'])}) "
                            f"{WINDOW_WORD[res.window]}."))  # fmt: skip
        n = len(res.scope_types)
        plural = "s" if n != 1 else ""
        cover = f"This covers {n} account{plural} worth ${o['base']:,.0f} at the start"
        if st.not_in_feed:
            missing = _join([ACCOUNT_LABEL[x] for x in st.not_in_feed])
            cover += (f", {n} of {n + len(st.not_in_feed)} accounts on record: the {missing} "
                      "is not in the data feed")  # fmt: skip
        out.append(("coverage", cover + "."))
        if st.timed_out:
            lost = _join([ACCOUNT_LABEL[x] for x in st.timed_out])
            out.append(("partial", f"Positions for the {lost} timed out, so they are not "
                                   "included."))  # fmt: skip
        if st.discrepancies:
            which = ", ".join(st.discrepancies)
            out.append(("discrepancy", f"Custodian and vendor prices for {which} differ by more "
                                       "than 1%; I used the custodian price."))  # fmt: skip
        estimated = [r for r in st.rows if r["source"] in ESTIMATE_SOURCES]
        if estimated:
            funds = sorted({f"{self.w.ticker(r['instrument_id'])} ({r['method']})"
                            for r in estimated if r["source"] == "nav_estimate"})  # fmt: skip
            foreign = sorted({self.w.ticker(r["instrument_id"]) for r in estimated
                              if r["source"] == "fair_value"})  # fmt: skip
            parts = []
            if funds:
                parts.append("mutual funds are priced once a day, so their values are estimated "
                             f"from proxies: {', '.join(funds)}")  # fmt: skip
            if foreign:
                parts.append(
                    f"the home market of {', '.join(foreign)} is closed, so it is a "
                    "fair-value estimate from the last close and exchange rate"
                )
            out.append(("estimate", f"This is an estimate: {'; '.join(parts)}."))
        if st.benchmark is not None and "relative_pct" in o:
            b = st.benchmark
            bname = BENCH_NAME.get(b) or f"a {b[3:5]}/{b[-2:]} blend of SPY and BND"
            why = ", which fits the client's mix" if res.benchmark == MIX else ""
            bpct, rpct = derive.fmt_pct(o["benchmark_pct"]), derive.fmt_pct(o["relative_pct"])
            out.append(("benchmark", f"Compared with {bname}{why}: the benchmark is {bpct}, so "
                                     f"the portfolio is {rpct} relative to it."))  # fmt: skip
        if st.yields is not None:
            y = st.yields
            if abs(y["ust10y_change_bps"]) < 1:
                out.append(("rates", "The 10-year Treasury yield was about unchanged on the last "
                                     "trading day."))  # fmt: skip
            else:
                word = "rose" if y["ust10y_change_bps"] > 0 else "fell"
                out.append(("rates", f"The 10-year Treasury yield {word} "
                                     f"{abs(y['ust10y_change_pct']):.2f}% "
                                     f"({abs(y['ust10y_change_bps']):.0f} bps) on the last "
                                     "trading day; bond prices move the other way."))  # fmt: skip
        if res.intent == "market_concern":
            head = news["headlines"][0].lower() if news else "markets are mixed"
            out.append(("context", "Whether this matters depends on the client's goals and time "
                                   f"horizon, which is a conversation for you as their advisor. "
                                   f"Today's news: {head}."))  # fmt: skip
        elif res.intent == "sell_inquiry":
            out.append(("context", "I can't make a buy or sell call; that decision is for you "
                                   "and the client, based on their plan."))  # fmt: skip
        elif res.intent == "tax_inquiry":
            out.append(("context", "Whether a loss can be used for taxes depends on cost basis "
                                   "and the client's situation; please check with a tax "
                                   "professional."))  # fmt: skip
        out.append(("disclosure", derive.DISCLOSURE))
        return out
