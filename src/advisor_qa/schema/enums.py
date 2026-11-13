"""Shared enums for the schema. Test IDs, instrument types and intents are added when needed."""

from enum import StrEnum


class Source(StrEnum):
    """Where a field's value comes from."""

    AGENT_TRACE = "agent_trace"  # what the agent logs
    EXTERNAL = "external"  # CRM or data feed
    DERIVED = "derived"  # computed by the QA layer from trace and answer
    GENERATED = "generated"  # written by the generator (hidden labels)
