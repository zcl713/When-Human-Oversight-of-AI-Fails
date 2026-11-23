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

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np

from advisor_qa import derive
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
from advisor_qa.schema.trace import Alternative, Holding, RunTrace, ToolCall
from advisor_qa.store.runs import output_hash
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
    trace: RunTrace
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
    calc.output = calculate_portfolio_impact(rows, flows, bench)


def price_row(position: dict[str, Any], quote: dict[str, Any], kind: str) -> dict[str, Any]:
    """The correct choice of price for one holding, from its quote or NAV record."""
    row = {**position, "start_price": quote["start_price"], "kind": kind}
    if kind == "etf":
        row.update(price=quote["price"], as_of=quote["as_of"], source="feed", tool="get_quotes")
    elif kind == "foreign":
        row.update(price=quote["fair_value_estimate"], as_of=quote["as_of_now"],
                   source="fair_value_estimate", method=quote["fair_value_method"],
                   tool="get_quotes")  # fmt: skip
    elif kind == "money_market":
        row.update(price=quote["nav"], as_of=quote["estimate_as_of"], source="stable_nav",
                   tool="get_fund_navs")  # fmt: skip
    else:
        row.update(price=quote["estimate"], as_of=quote["estimate_as_of"], source="proxy_estimate",
                   method=quote["estimate_method"], tool="get_fund_navs")  # fmt: skip
    return row


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
        calls: list[ToolCall] = []
        outputs: dict[str, dict[str, Any]] = {}

        def call(name: str, args: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
            status = "ok"
            for hook in f.tool:
                status = hook(name, out, st) or status
            h = output_hash(out)
            latency = round(LATENCY_MS.get(name, 200) * float(np.exp(st.rng.normal(0, 0.25))), 1)
            if status == "timeout":
                latency = round(latency + 30_000, 1)
            calls.append(ToolCall(tool_name=name, args=args, status=status, latency_ms=latency,
                                  output_hash=h))  # fmt: skip
            outputs[h] = out
            return out

        hh, ts, t = res.household_id, q.timestamp, st.tools
        crm = self.w.account_table(hh)
        crm_types = [
            str(x) for x, ok in zip(crm["type"], crm["in_crm"], strict=True)
            if bool(ok) and x not in f.crm_drop_types
        ]  # fmt: skip
        feed_types = sorted(set(crm["type"]))
        calc: Calc | None = None
        news: dict[str, Any] | None = None
        inception: dict[str, Any] | None = None
        if res.intent != "general_reassurance":
            if q.names_client:
                name = surname(self.dir.name_of[q.household_id])
                call("search_clients", {"name": name}, t.search_clients(name))
            call("get_client_profile", {"household_id": hh}, t.get_client_profile(hh))
            acc_out = t.get_accounts(hh)
            acc_out["accounts"] = [
                a for a in acc_out["accounts"] if a["type"] not in f.crm_drop_types
            ]
            acc_out = call("get_accounts", {"household_id": hh}, acc_out)
            seen = {a["type"] for a in acc_out["accounts"]}
            res.scope_types = [x for x in res.scope_types if x in seen]
            ids_in_scope = [a["account_id"] for a in acc_out["accounts"]
                            if a["type"] in res.scope_types]  # fmt: skip
            pos_args = {"household_id": hh, "account_ids": ids_in_scope}
            pos_out = call("get_positions", pos_args, t.get_positions(hh, ids_in_scope))
            positions = pos_out["positions"]
            st.start_day = window_start(res.window, ts, self.w.prices.calendar.days) or ts.date()
            tx = call("get_transactions", {**pos_args, "since": ts.date().isoformat()},
                      t.get_transactions(hh, ids_in_scope, ts.date()))  # fmt: skip
            st.flows = round(sum(x["amount"] for x in tx["transactions"]), 2)
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
            calc = Calc(calculate_portfolio_impact(st.rows, st.flows, bench_pct),
                        tool_name=self.calc_tool(res.window))  # fmt: skip
            calc.args = {"holdings": len(st.rows), "period": res.window}
            for chook in f.calc:
                chook(calc, st)
            if calc.via_tool:
                call(calc.tool_name, calc.args, calc.output)
        if res.intent in ("market_concern", "general_reassurance"):
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
        trace = self._trace(q, res, st, calls, outputs, calc, answer, crm_types, feed_types)
        dollars = [v for _, v, k in derive.parse_numbers(answer) if k == "dollars"]
        return RunResult(trace, answer, dollars[0] if dollars else None, res)

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
        out.append(("coverage", f"This covers {len(res.scope_types)} account"
                                f"{'s' if len(res.scope_types) != 1 else ''} worth "
                                f"${o['base']:,.0f} at the start."))  # fmt: skip
        estimated = [r for r in st.rows if r["source"] in ESTIMATE_SOURCES]
        if estimated:
            funds = sorted({f"{self.w.ticker(r['instrument_id'])} ({r['method']})"
                            for r in estimated if r["source"] == "proxy_estimate"})  # fmt: skip
            foreign = sorted({self.w.ticker(r["instrument_id"]) for r in estimated
                              if r["source"] == "fair_value_estimate"})  # fmt: skip
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

    # --- trace ----------------------------------------------------------------------------------

    def expected_tools(self, q: Question, rows: list[dict[str, Any]]) -> list[str]:
        """What the QA layer expects, from the prompt: the intent's tools, the calculation tool
        for the window the prompt names (today if none), the price tools the holdings need."""
        e = self.p.expected_tools
        if q.intent == "general_reassurance":
            return list(e["general_reassurance"])
        window = derive.requested_window(q.prompt) or "intraday"
        if q.intent == "portfolio_performance":
            window = "ytd"
        out = list(e["core"]) + list(e.get(q.intent, []))
        if derive.named_surname(q.prompt):
            out.insert(0, "search_clients")
        if not any(r["tool"] == "get_quotes" for r in rows) and "get_quotes" in out:
            out.remove("get_quotes")
        if any(r["tool"] == "get_fund_navs" for r in rows):
            out.append("get_fund_navs")
        out.append(self.calc_tool(window))
        return list(dict.fromkeys(out))

    def _trace(
        self,
        q: Question,
        res: Resolution,
        st: RunState,
        calls: list[ToolCall],
        outputs: dict[str, dict[str, Any]],
        calc: Calc | None,
        answer: str,
        crm_types: list[str],
        feed_types: list[str],
    ) -> RunTrace:
        # link figures to the calculation output first, then the other tool outputs
        calc_first = dict(sorted(outputs.items(), key=lambda kv: "dollar_impact" not in kv[1]))
        links = derive.link_numbers(answer, calc_first)
        o = calc.output if calc else {}
        start_prices = {r["instrument_id"]: r["start_price"] for r in st.rows}
        inputs: dict[str, Any] = {}
        intermediate: dict[str, Any] = {}
        if calc is not None:
            inputs = {
                "positions": len(st.rows),
                "start_prices": start_prices,
                "window_start": st.start_day.isoformat() if st.start_day else None,
                "net_flows_today": round(st.flows, 2),
            }
            intermediate = {
                "covered_value": o.get("base", 0.0),
                "by_holding": o.get("by_holding", {}),
                "flows_excluded": o.get("flows_excluded", True),
                "estimate_methods": {
                    r["instrument_id"]: r["method"] for r in st.rows if "method" in r
                },  # fmt: skip
                **calc.intermediate,
            }
            if st.yields is not None:
                intermediate["ust10y_change_bps"] = st.yields["ust10y_change_bps"]
        pos_outs = [x for x in outputs.values() if "snapshot_id" in x]
        snapshot = pos_outs[0]["snapshot_id"] if pos_outs else ""
        return RunTrace(
            run_id=q.run_id,
            timestamp=q.timestamp,
            household_id=res.household_id,
            advisor_id=q.advisor_id,
            prompt=q.prompt,
            conversation_turn=q.turn,
            resolved_intent=res.intent,
            alternatives_considered=[Alternative(intent=i, score=s) for i, s in res.alternatives],
            accounts_requested=derive.requested_accounts(
                q.prompt, sorted(set(crm_types) | set(feed_types))
            ),
            accounts_resolved=list(res.scope_types) if calc else [],
            time_window=res.window,
            benchmark_index=st.benchmark,
            interpretation_restated=derive.is_restated(answer),
            tool_calls=calls,
            expected_tool_set=self.expected_tools(q, st.rows),
            holdings=[
                Holding(
                    account_id=r["account_id"],
                    instrument_id=r["instrument_id"],
                    qty=r["qty"],
                    price=r["price"],
                    price_as_of=datetime.fromisoformat(r["as_of"]),
                    price_source=r["source"],
                    instrument_type=r["instrument_type"],
                )
                for r in st.rows
            ],
            accounts_in_crm=sorted(crm_types),
            accounts_in_feed=feed_types,
            data_snapshot_id=snapshot,
            inputs=inputs,
            base=float(o.get("base", 0.0)),
            calc_as_of=q.timestamp,
            computed_by="tool" if calc is not None and calc.computed_by == "tool" else "llm",
            intermediate_values=intermediate,
            numbers_claimed=[v for _, v, _ in derive.parse_numbers(answer)],
            number_to_tool_output_links=links,
            certainty_and_advice_phrases=derive.phrases(answer),
            estimate_label=derive.has_estimate_label(answer),
            disclosures_present=derive.has_disclosure(answer),
        )
