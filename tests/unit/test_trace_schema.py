import json
from pathlib import Path

from advisor_qa.schema.trace import RunTrace

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_clean_run_parses_and_round_trips() -> None:
    raw = json.loads((FIXTURES / "clean_run.json").read_text())

    trace = RunTrace.model_validate(raw)

    assert trace.household_id == "HH-004217"
    assert len(trace.holdings) == 6
    assert len(trace.tool_calls) == 4
    assert RunTrace.model_validate_json(trace.model_dump_json()) == trace
