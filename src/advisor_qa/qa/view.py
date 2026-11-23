"""Derive the 39-field RunTrace from one run's OpenTelemetry spans.

Everything comes from what the instrumentation recorded: the root `invoke_agent` span (prompt,
answer, the agent's own intent decision), the `execute_tool` spans (arguments, results, status,
duration). Derived fields (accounts requested, expected tools, number links, phrases) come from
`advisor_qa.derive`. Nothing here can see labels, true prices or the injectors.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from advisor_qa import derive
from advisor_qa.otel.emit import _dst
from advisor_qa.otel.model import STATUS_ERROR, Span, TracesData
from advisor_qa.pipeline.params import PipelineParams
from advisor_qa.schema.trace import Alternative, Holding, RunTrace, ToolCall
from advisor_qa.store.runs import output_hash

CALC_TOOLS = ("calculate_portfolio_impact", "get_performance")


def local_time(nanos: int) -> datetime:
    """Unix nanoseconds -> New York wall-clock time."""
    utc = datetime(1970, 1, 1) + timedelta(microseconds=nanos // 1000)
    local = utc - timedelta(hours=5)
    return utc - timedelta(hours=4) if _dst(local.date()) else local


@dataclass(frozen=True)
class ToolSpan:
    name: str
    args: dict[str, Any]
    result: Any
    ok: bool
    error_type: str | None
    duration_ms: float
    span: Span


def tool_spans(trace: TracesData) -> list[ToolSpan]:
    spans = sorted(trace.by_operation("execute_tool"), key=lambda s: int(s.startTimeUnixNano))
    return [
        ToolSpan(
            name=str(s.attr("gen_ai.tool.name")),
            args=s.json_attr("gen_ai.tool.call.arguments", {}),
            result=s.json_attr("gen_ai.tool.call.result"),
            ok=s.status.code != STATUS_ERROR,
            error_type=s.attr("error.type"),
            duration_ms=s.duration_ms,
            span=s,
        )
        for s in spans
    ]


def _text(messages_json: Any) -> str:
    msgs = json.loads(messages_json) if isinstance(messages_json, str) else []
    return " ".join(p.get("content", "") for m in msgs for p in m.get("parts", [])
                    if p.get("type") == "text")  # fmt: skip


def prompt_and_answer(trace: TracesData) -> tuple[str, str]:
    root = trace.root()
    return _text(root.attr("gen_ai.input.messages")), _text(root.attr("gen_ai.output.messages"))


def calc_span(tools: list[ToolSpan]) -> ToolSpan | None:
    return next((x for x in tools if x.name in CALC_TOOLS
                 and x.args.get("period") != "inception"), None)  # fmt: skip


def _first(tools: list[ToolSpan], name: str) -> ToolSpan | None:
    return next((x for x in tools if x.name == name), None)


def _default_holdings(tools: list[ToolSpan], ts: datetime) -> list[dict[str, Any]]:
    """Without a calculation tool call, rebuild holdings the way the platform prices them:
    live quotes, fund estimates, fair values."""
    pos = _first(tools, "get_positions")
    prices: dict[str, dict[str, Any]] = {}
    q, n = _first(tools, "get_quotes"), _first(tools, "get_fund_navs")
    for ins, x in ((q.result or {}).get("quotes", {}) if q else {}).items():
        if "fair_value_estimate" in x:
            prices[ins] = {
                "price": x["fair_value_estimate"],
                "price_as_of": ts.isoformat(),
                "price_source": "fair_value",
                "start_price": x["start_price"],
            }
        else:
            prices[ins] = {
                "price": x["price"],
                "price_as_of": x["as_of"],
                "price_source": "live",
                "start_price": x["start_price"],
            }
    for ins, x in ((n.result or {}).get("funds", {}) if n else {}).items():
        stable = x.get("estimate_method", "").startswith("stable")
        prices[ins] = {"price": x["estimate"], "price_as_of": x["estimate_as_of"],
                       "price_source": "stable_nav" if stable else "nav_estimate",
                       "start_price": x["start_price"]}  # fmt: skip
    out = []
    for p in (pos.result or {}).get("positions", []) if pos else []:
        if p["instrument_id"] in prices:
            out.append({**p, **prices[p["instrument_id"]]})
    return out


def expected_tools(prompt: str, holdings: list[dict[str, Any]], p: PipelineParams) -> list[str]:
    """What the QA layer expects from the prompt alone, plus the price tools the holdings need."""
    e = p.expected_tools
    intent = derive.prompt_intent(prompt)
    window = derive.requested_window(prompt) or "intraday"
    if intent == "portfolio_performance":
        window = "ytd"
    out = list(e["core"]) + list(e.get(intent, []))
    if derive.named_surname(prompt):
        out.insert(0, "search_clients")
    sources = {h["price_source"] for h in holdings}
    if not sources & {"live", "fair_value", "home_close", "vendor"} and "get_quotes" in out:
        out.remove("get_quotes")
    if sources & {"nav_estimate", "stable_nav", "nav"}:
        out.append("get_fund_navs")
    out.append(p.calc_tools["intraday" if window == "intraday" else "period"])
    return list(dict.fromkeys(out))


def derive_view(trace: TracesData, params: PipelineParams) -> RunTrace:
    root = trace.root()
    ts = local_time(int(root.startTimeUnixNano))
    prompt, answer = prompt_and_answer(trace)
    tools = tool_spans(trace)
    calc = calc_span(tools)
    holdings = list(calc.args.get("holdings", [])) if calc else _default_holdings(tools, ts)
    acc = _first(tools, "get_accounts")
    pos = _first(tools, "get_positions")
    crm_types = sorted({a["type"] for a in ((acc.result or {}).get("accounts", []) if acc else [])})
    feed_types = sorted((pos.result or {}).get("accounts_in_feed", []) if pos else [])
    tx = _first(tools, "get_transactions")
    flows = sum(x["amount"] for x in ((tx.result or {}).get("transactions", []) if tx else []))
    bench = _first(tools, "get_benchmark_returns")
    navs = _first(tools, "get_fund_navs")
    yields = _first(tools, "get_treasury_yields")
    outputs = {output_hash(x.result): x.result for x in tools if isinstance(x.result, dict)}
    calc_first = dict(sorted(outputs.items(), key=lambda kv: "dollar_impact" not in kv[1]))
    start_prices = {h["instrument_id"]: h["start_price"] for h in holdings}
    base = sum(h["qty"] * h["start_price"] for h in holdings)
    inputs: dict[str, Any] = {}
    intermediate: dict[str, Any] = {}
    if holdings:
        inputs = {"positions": len(holdings), "start_prices": start_prices,
                  "net_flows_today": round(flows, 2)}  # fmt: skip
    if calc is not None:
        result = calc.result or {}
        base = float(result.get("base", base))
        inputs.update({"period": calc.args.get("period")})
        intermediate = {"covered_value": result.get("base"),
                        "by_holding": result.get("by_holding", {})}  # fmt: skip
    if navs is not None:
        intermediate["estimate_methods"] = {
            i: x["estimate_method"] for i, x in (navs.result or {}).get("funds", {}).items()
        }
    if yields is not None and yields.result:
        intermediate["ust10y_change_bps"] = yields.result.get("ust10y_change_bps")
    snapshot = (pos.result or {}).get("snapshot_id", "") if pos else ""
    resolved = root.attr("advisor_qa.scope.account_types", []) or []
    return RunTrace(
        run_id=root.traceId,
        timestamp=ts,
        household_id=str(root.attr("advisor_qa.household.id")),
        advisor_id=str(root.attr("advisor_qa.advisor.id")),
        prompt=prompt,
        conversation_turn=int(root.attr("advisor_qa.conversation.turn", 1)),
        resolved_intent=str(root.attr("advisor_qa.intent.resolved")),
        alternatives_considered=[
            Alternative(**a) for a in root.json_attr("advisor_qa.intent.candidates", [])
        ],  # fmt: skip
        accounts_requested=derive.requested_accounts(
            prompt, sorted(set(crm_types) | set(feed_types))
        ),  # fmt: skip
        accounts_resolved=list(resolved) if holdings else [],
        time_window=str(root.attr("advisor_qa.time_window")),
        benchmark_index=bench.args.get("index") if bench else None,
        interpretation_restated=derive.is_restated(answer),
        tool_calls=[
            ToolCall(
                tool_name=x.name,
                args=x.args,
                status="ok" if x.ok else str(x.error_type),
                latency_ms=round(x.duration_ms, 1),
                output_hash=output_hash(x.result),
            )
            for x in tools
        ],  # fmt: skip
        expected_tool_set=expected_tools(prompt, holdings, params),
        holdings=[
            Holding(
                account_id=h["account_id"],
                instrument_id=h["instrument_id"],
                qty=h["qty"],
                price=h["price"],
                price_as_of=datetime.fromisoformat(h["price_as_of"]),
                price_source=h["price_source"],
                instrument_type=h["instrument_type"],
            )
            for h in holdings
        ],  # fmt: skip
        accounts_in_crm=crm_types,
        accounts_in_feed=feed_types,
        data_snapshot_id=snapshot,
        inputs=inputs,
        base=round(float(base), 2),
        calc_as_of=ts,
        computed_by="tool" if calc is not None else "llm",
        intermediate_values=intermediate,
        numbers_claimed=[v for _, v, _ in derive.parse_numbers(answer)],
        number_to_tool_output_links=derive.link_numbers(answer, calc_first),
        certainty_and_advice_phrases=derive.phrases(answer),
        estimate_label=derive.has_estimate_label(answer),
        disclosures_present=derive.has_disclosure(answer),
    )
