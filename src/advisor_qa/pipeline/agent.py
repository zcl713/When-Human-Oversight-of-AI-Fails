"""The correct agent: resolves the question, calls the mock tools, answers from their outputs.

Error injection never edits a finished trace. An injector configures `Faults` (hooks on intent
resolution, tool outputs, the calculation, the answer, and extra tool calls) and the same agent
runs with them, so every trace stays internally consistent: hashes match outputs, numbers match
the calculation that produced them, and the answer text matches the derived Answer fields.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np

from advisor_qa import derive
from advisor_qa.pipeline.directory import Directory
from advisor_qa.pipeline.params import PipelineParams
from advisor_qa.pipeline.questions import ACCOUNT_LABEL, AMBIGUOUS_KINDS, Question
from advisor_qa.pipeline.tools import ESTIMATE_SOURCES, Tools, World, calculate_portfolio_impact
from advisor_qa.schema.trace import Alternative, Holding, RunTrace, ToolCall
from advisor_qa.store.runs import output_hash
from advisor_qa.truth.impact import window_start
from advisor_qa.world.streams import stream

WINDOW_WORD = {"intraday": "today", "wtd": "this week", "mtd": "this month", "ytd": "this year"}
BENCH_NAME = {"SPY": "The S&P 500 (SPY)", "VTI": "The total market (VTI)", "BND": "US bonds (BND)"}
LATENCY_MS = {
    "get_client_accounts": 120,
    "get_positions": 210,
    "get_market_prices": 340,
    "calculate_portfolio_impact": 45,
    "get_benchmark": 150,
    "get_market_news": 260,
    "get_client_notes": 90,
}
ALTERNATIVE_OF = {
    "portfolio_impact": "portfolio_performance_ytd",
    "benchmark_comparison": "portfolio_impact",
    "market_concern": "general_reassurance",
    "sell_inquiry": "trade_request",
    "tax_inquiry": "tax_advice",
}


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
    computed_by: str = "tool"  # "tool" or "llm"
    via_tool: bool = True  # False: the agent did the arithmetic itself (no calc tool call)
    intermediate: dict[str, Any] = field(default_factory=dict)


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
    calc: "Calc | None" = None


Sentence = list[tuple[str, str]]  # (tag, text), in order
ResolveHook = Callable[[Resolution, RunState], None]
ToolHook = Callable[[str, dict[str, Any], RunState], str | None]  # mutate output; status
CalcHook = Callable[[Calc, RunState], None]
AnswerHook = Callable[[Sentence, RunState], None]
ExtraCall = Callable[[RunState], tuple[str, dict[str, Any], dict[str, Any]] | None]


@dataclass
class Faults:
    resolve: list[ResolveHook] = field(default_factory=list)
    crm_drop_types: set[str] = field(default_factory=set)  # accounts missing from the CRM
    tool: list[ToolHook] = field(default_factory=list)
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
            base_ms = LATENCY_MS.get(name, 200)
            latency = round(base_ms * float(np.exp(st.rng.normal(0, 0.25))), 1)
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
        bench_pct: float | None = None
        news: dict[str, Any] | None = None
        if res.intent != "general_reassurance":
            acc_out = t.get_client_accounts(hh)
            acc_out["accounts"] = [
                a for a in acc_out["accounts"] if a["type"] not in f.crm_drop_types
            ]
            acc_out = call("get_client_accounts", {"household_id": hh}, acc_out)
            seen = {a["type"] for a in acc_out["accounts"]}
            res.scope_types = [x for x in res.scope_types if x in seen]
            pos_out = call("get_positions", {"household_id": hh}, t.get_positions(hh))
            positions = [p for p in pos_out["positions"] if p["account_type"] in res.scope_types]
            days = self.w.prices.calendar.days
            st.start_day = window_start(res.window, ts, days) or ts.date()
            ids = sorted({p["instrument_id"] for p in positions})
            args: dict[str, Any] = {"as_of": ts.isoformat(), "instrument_ids": ids}
            if res.window != "intraday":
                args["since"] = st.start_day.isoformat()
            px_out = call("get_market_prices", args, t.get_market_prices(ids, st.start_day))
            for p in positions:
                quote = px_out["prices"].get(p["instrument_id"])
                if quote is None:
                    continue
                st.rows.append({**p, **quote})
            scoped_ids = {self.w.account_id(hh, x) for x in res.scope_types}
            st.flows = sum(
                fl["amount"] for fl in pos_out["cash_flows_today"]
                if fl["account_id"] in scoped_ids
            )  # fmt: skip
            if res.intent == "benchmark_comparison":
                b = res.benchmark or "SPY"
                b_args = {"index": b, "as_of": ts.isoformat()}
                bench_pct = call("get_benchmark", b_args, t.get_benchmark(b, st.start_day))[
                    "return_pct"
                ]
            calc = Calc(calculate_portfolio_impact(st.rows, st.flows, bench_pct))
            for chook in f.calc:
                chook(calc, st)
            if calc.via_tool:
                c_args = {"holdings": len(st.rows), "window": res.window}
                call("calculate_portfolio_impact", c_args, calc.output)
            if res.intent in ("sell_inquiry", "tax_inquiry"):
                call("get_client_notes", {"household_id": hh}, t.get_client_notes(hh))
        if res.intent in ("market_concern", "general_reassurance"):
            news = call("get_market_news", {"date": ts.date().isoformat()}, t.get_market_news())
        for extra in f.extra_calls:
            got = extra(st)
            if got is not None:
                call(*got)

        st.calc = calc
        sentences = self.compose(q, res, st, calc, bench_pct, news, crm_types)
        for ahook in f.answer:
            ahook(sentences, st)
        answer = " ".join(s for _, s in sentences if s)
        trace = self._trace(q, res, st, calls, outputs, calc, answer, crm_types, feed_types)
        dollars = [v for _, v, k in derive.parse_numbers(answer) if k == "dollars"]
        return RunResult(trace, answer, dollars[0] if dollars else None, res)

    # --- answer ---------------------------------------------------------------------------------

    def compose(
        self,
        q: Question,
        res: Resolution,
        st: RunState,
        calc: Calc | None,
        bench_pct: float | None,
        news: dict[str, Any] | None,
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
            out.append(("restate", f"{derive.RESTATE_MARK} the change {since} across "
                                   f"{scope_desc}."))  # fmt: skip
        o = calc.output
        out.append(("main", f"As of {_clock(q.timestamp)} ET, {name}'s household has changed by "
                            f"{derive.fmt_dollars(o['dollar_impact'])} "
                            f"({derive.fmt_pct(o['pct_impact'])}) "
                            f"{WINDOW_WORD[res.window]}."))  # fmt: skip
        if any(r["source"] in ESTIMATE_SOURCES for r in st.rows):
            out.append(("estimate", "Fund and foreign prices are indicative until the 4:00 PM "
                                    "strike, so this is an estimate."))  # fmt: skip
        if bench_pct is not None and "relative_pct" in o:
            out.append(("benchmark", f"{BENCH_NAME[res.benchmark or 'SPY']} is "
                                     f"{derive.fmt_pct(bench_pct)} over the same period, so the "
                                     f"portfolio is {derive.fmt_pct(o['relative_pct'])} relative "
                                     "to it."))  # fmt: skip
        if res.intent == "market_concern":
            head = news["headlines"][0].lower() if news else "markets are mixed"
            out.append(("context", "Whether this matters depends on the client's goals and time "
                                   f"horizon. Today's news: {head}."))  # fmt: skip
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
        intent_tools = self.p.expected_tools.get(res.intent, [])
        expected = self.p.expected_tools.get(q.intent, intent_tools)
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
                **calc.intermediate,
            }
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
            benchmark_index=res.benchmark if res.intent == "benchmark_comparison" else None,
            interpretation_restated=derive.is_restated(answer),
            tool_calls=calls,
            expected_tool_set=list(expected),
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
