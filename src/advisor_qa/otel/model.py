"""OTLP/JSON trace data (opentelemetry-proto `TracesData`), as plain Pydantic models.

No dependency on the OpenTelemetry SDK or protobuf: the JSON mapping is small, and compiled
libraries do not load on every team machine (see README). Field names and encodings follow the
OTLP JSON rules: camelCase keys, lowercase hex trace and span ids, int64 values (timestamps,
intValue) as decimal strings, enums as integers.
"""

import json
from collections.abc import Iterator, Mapping
from typing import Any

from pydantic import BaseModel, Field

SEMCONV_GENAI_VERSION = "0.1.0"  # open-telemetry/semantic-conventions-genai
SCHEMA_URL = "https://opentelemetry.io/schemas/1.45.0"  # base semconv the GenAI repo builds on

SPAN_KIND_INTERNAL, SPAN_KIND_CLIENT = 1, 3
STATUS_UNSET, STATUS_OK, STATUS_ERROR = 0, 1, 2


class AnyValue(BaseModel):
    stringValue: str | None = None
    boolValue: bool | None = None
    intValue: str | None = None
    doubleValue: float | None = None
    arrayValue: "ArrayValue | None" = None

    def py(self) -> Any:
        if self.stringValue is not None:
            return self.stringValue
        if self.boolValue is not None:
            return self.boolValue
        if self.intValue is not None:
            return int(self.intValue)
        if self.doubleValue is not None:
            return self.doubleValue
        if self.arrayValue is not None:
            return [v.py() for v in self.arrayValue.values]
        return None


class ArrayValue(BaseModel):
    values: list[AnyValue] = []


class KeyValue(BaseModel):
    key: str
    value: AnyValue


def any_value(v: Any) -> AnyValue:
    """Python value -> AnyValue. Structured values (dicts, lists of dicts) become JSON strings,
    as the GenAI conventions allow for `any` attributes when structured values are unsupported."""
    if isinstance(v, bool):
        return AnyValue(boolValue=v)
    if isinstance(v, int):
        return AnyValue(intValue=str(v))
    if isinstance(v, float):
        return AnyValue(doubleValue=v)
    if isinstance(v, str):
        return AnyValue(stringValue=v)
    if isinstance(v, list | tuple) and all(isinstance(x, str | int | float | bool) for x in v):
        return AnyValue(arrayValue=ArrayValue(values=[any_value(x) for x in v]))
    return AnyValue(stringValue=json.dumps(v, sort_keys=True, separators=(",", ":"), default=str))


def attributes(d: Mapping[str, Any]) -> list[KeyValue]:
    return [KeyValue(key=k, value=any_value(v)) for k, v in d.items() if v is not None]


class Status(BaseModel):
    code: int = STATUS_UNSET
    message: str | None = None


class Event(BaseModel):
    timeUnixNano: str
    name: str
    attributes: list[KeyValue] = []


class Span(BaseModel):
    traceId: str
    spanId: str
    parentSpanId: str | None = None
    name: str
    kind: int
    startTimeUnixNano: str
    endTimeUnixNano: str
    attributes: list[KeyValue] = []
    events: list[Event] = []
    status: Status = Field(default_factory=Status)

    def attr(self, key: str, default: Any = None) -> Any:
        for kv in self.attributes:
            if kv.key == key:
                return kv.value.py()
        return default

    def json_attr(self, key: str, default: Any = None) -> Any:
        raw = self.attr(key)
        return json.loads(raw) if isinstance(raw, str) else default

    @property
    def duration_ms(self) -> float:
        return (int(self.endTimeUnixNano) - int(self.startTimeUnixNano)) / 1e6


class InstrumentationScope(BaseModel):
    name: str
    version: str | None = None


class ScopeSpans(BaseModel):
    scope: InstrumentationScope
    spans: list[Span] = []
    schemaUrl: str | None = None


class Resource(BaseModel):
    attributes: list[KeyValue] = []


class ResourceSpans(BaseModel):
    resource: Resource
    scopeSpans: list[ScopeSpans] = []
    schemaUrl: str | None = None


class TracesData(BaseModel):
    """One run's trace (one root `invoke_agent` span and its children)."""

    resourceSpans: list[ResourceSpans] = []

    def spans(self) -> Iterator[Span]:
        for rs in self.resourceSpans:
            for ss in rs.scopeSpans:
                yield from ss.spans

    def root(self) -> Span:
        return next(s for s in self.spans() if s.parentSpanId is None)

    def by_operation(self, op: str) -> list[Span]:
        return [s for s in self.spans() if s.attr("gen_ai.operation.name") == op]


AnyValue.model_rebuild()
