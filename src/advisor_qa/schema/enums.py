"""Shared enums for the schema. Test IDs, instrument types and intents are added when needed."""

from enum import StrEnum


class FailureModeId(StrEnum):
    """Test IDs from the client suite: data (D), calculation (C), intent (I), overconfidence (O),
    security (S). TODO: "wrong tool" has no ID yet (undecided; the old prototype used T1)."""

    D1 = "D1"
    D2 = "D2"
    D3 = "D3"
    D4 = "D4"
    D5 = "D5"
    D6 = "D6"
    D7 = "D7"
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"
    C5 = "C5"
    C6 = "C6"
    C7 = "C7"
    I1 = "I1"
    I2 = "I2"
    I3 = "I3"
    I4 = "I4"
    I5 = "I5"
    I6 = "I6"
    O1 = "O1"
    O2 = "O2"
    O3 = "O3"
    O4 = "O4"
    O5 = "O5"
    O6 = "O6"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"


class Source(StrEnum):
    """Where a field's value comes from."""

    AGENT_TRACE = "agent_trace"  # what the agent logs
    EXTERNAL = "external"  # CRM or data feed
    DERIVED = "derived"  # computed by the QA layer from trace and answer
    GENERATED = "generated"  # written by the generator (hidden labels)
