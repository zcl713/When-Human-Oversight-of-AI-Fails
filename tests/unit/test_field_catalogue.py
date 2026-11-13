from collections import Counter

from advisor_qa.schema.enums import Source
from advisor_qa.schema.trace import schema_fields

# Counts copied from the hand-off, section 5 (Review group removed).
EXPECTED_GROUP_COUNTS = {
    "Run": 6,
    "Intent and scope": 7,
    "Tool calls": 6,
    "Data, per holding": 5,
    "Data, per run": 3,
    "Calculation": 5,
    "Answer": 5,
}


def test_trace_has_37_fields_in_7_groups() -> None:
    fields = schema_fields()

    assert len(fields) == 37
    assert dict(Counter(f.group for f in fields)) == EXPECTED_GROUP_COUNTS


def test_every_field_has_a_source_category() -> None:
    by_name = {f.name: f for f in schema_fields()}

    assert all(isinstance(f.source, Source) for f in by_name.values())
    assert by_name["run_id"].source is Source.AGENT_TRACE
    assert by_name["household_id"].source is Source.EXTERNAL
    assert by_name["alternatives_considered"].source is Source.DERIVED
