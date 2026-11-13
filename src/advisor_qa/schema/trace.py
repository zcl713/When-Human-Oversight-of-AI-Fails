"""Run trace: what the QA layer sees for one agent run (hand-off section 5, 37 fields).

Every field carries its group and source category as metadata. The hidden error label lives in
`schema.labels`, never here.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from advisor_qa.schema.enums import Source

A, E, D = Source.AGENT_TRACE, Source.EXTERNAL, Source.DERIVED


def meta(group: str, source: Source) -> Any:
    """Field with group and source category recorded as metadata."""
    return Field(json_schema_extra={"group": group, "source": source.value})


def expand(item_model: str) -> Any:
    """Marks a list field whose item model carries the per-field metadata."""
    return Field(json_schema_extra={"expand": item_model})


class Alternative(BaseModel):
    intent: str
    score: float


class ToolCall(BaseModel):
    tool_name: str = meta("Tool calls", A)
    args: dict[str, Any] = meta("Tool calls", A)
    status: str = meta("Tool calls", A)
    latency_ms: float = meta("Tool calls", A)
    output_hash: str = meta("Tool calls", A)


class Holding(BaseModel):
    qty: float = meta("Data, per holding", E)
    price: float = meta("Data, per holding", E)
    price_as_of: datetime = meta("Data, per holding", E)
    price_source: str = meta("Data, per holding", E)
    instrument_type: str = meta("Data, per holding", E)


class NumberLink(BaseModel):
    """A claimed number linked to the tool output it came from. TODO(stage 3): confirm shape."""

    number: float
    output_hash: str


class RunTrace(BaseModel):
    # Run
    run_id: str = meta("Run", A)
    timestamp: datetime = meta("Run", A)
    household_id: str = meta("Run", E)
    advisor_id: str = meta("Run", E)
    prompt: str = meta("Run", A)
    conversation_turn: int = meta("Run", A)
    # Intent and scope
    resolved_intent: str = meta("Intent and scope", A)
    alternatives_considered: list[Alternative] = meta("Intent and scope", D)
    accounts_requested: list[str] = meta("Intent and scope", D)
    accounts_resolved: list[str] = meta("Intent and scope", A)
    time_window: str = meta("Intent and scope", A)
    benchmark_index: str | None = meta("Intent and scope", A)
    interpretation_restated: bool = meta("Intent and scope", D)
    # Tool calls (one entry per call; fields defined on ToolCall)
    tool_calls: list[ToolCall] = expand("ToolCall")
    expected_tool_set: list[str] = meta("Tool calls", D)
    # Data, per holding (one entry per holding; fields defined on Holding)
    holdings: list[Holding] = expand("Holding")
    # Data, per run
    accounts_in_crm: list[str] = meta("Data, per run", E)
    accounts_in_feed: list[str] = meta("Data, per run", E)
    data_snapshot_id: str = meta("Data, per run", E)
    # Calculation
    inputs: dict[str, Any] = meta("Calculation", A)
    base: float = meta("Calculation", A)
    calc_as_of: datetime = meta("Calculation", A)
    computed_by: Literal["tool", "llm"] = meta("Calculation", A)
    intermediate_values: dict[str, Any] = meta("Calculation", A)
    # Answer
    numbers_claimed: list[float] = meta("Answer", D)
    number_to_tool_output_links: list[NumberLink] = meta("Answer", D)
    certainty_and_advice_phrases: list[str] = meta("Answer", D)
    estimate_label: bool = meta("Answer", D)
    disclosures_present: bool = meta("Answer", D)


@dataclass(frozen=True)
class FieldSpec:
    group: str
    name: str
    source: Source


_ITEM_MODELS: dict[str, type[BaseModel]] = {"ToolCall": ToolCall, "Holding": Holding}


def schema_fields() -> list[FieldSpec]:
    """Every trace field with its group and source category (list items expanded)."""
    specs: list[FieldSpec] = []

    def collect(model: type[BaseModel]) -> None:
        for name, info in model.model_fields.items():
            extra = info.json_schema_extra
            assert isinstance(extra, dict)
            if "expand" in extra:
                collect(_ITEM_MODELS[str(extra["expand"])])
            else:
                specs.append(FieldSpec(str(extra["group"]), name, Source(str(extra["source"]))))

    collect(RunTrace)
    return specs
