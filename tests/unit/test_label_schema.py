import pytest
from pydantic import ValidationError

from advisor_qa.schema.labels import RunLabel
from advisor_qa.schema.trace import RunTrace


def test_multi_error_label_is_accepted() -> None:
    label = RunLabel(
        run_id="3f2a9c1e5b7d4a80916c2e4d8b0f7a31",
        error_present=True,
        error_type=["D1", "D2", "C2"],
        root_cause_id=None,
        severity=8300.0,
    )

    assert label.error_type == ["D1", "D2", "C2"]


def test_clean_label_has_no_error_types() -> None:
    label = RunLabel(
        run_id="ab" * 16, error_present=False, error_type=[], root_cause_id=None, severity=0.0
    )

    assert not label.error_present


def test_unknown_test_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RunLabel(
            run_id="ab" * 16,
            error_present=True,
            error_type=["Z9"],
            root_cause_id=None,
            severity=1.0,
        )


def test_error_present_must_match_error_types() -> None:
    with pytest.raises(ValidationError):
        RunLabel(
            run_id="ab" * 16, error_present=True, error_type=[], root_cause_id=None, severity=1.0
        )
    with pytest.raises(ValidationError):
        RunLabel(
            run_id="ab" * 16,
            error_present=False,
            error_type=["D1"],
            root_cause_id=None,
            severity=1.0,
        )


def test_trace_has_no_label_fields() -> None:
    label_fields = {"error_present", "error_type", "root_cause_id", "severity"}

    assert not label_fields & set(RunTrace.model_fields)
