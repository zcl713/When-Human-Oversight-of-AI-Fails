"""Hidden error label, stored separately from traces and keyed by run_id.

Evaluation-only: nothing that reads traces to flag or score them may import this module.
"""

from typing import Self

from pydantic import BaseModel, model_validator

from advisor_qa.schema.enums import FailureModeId


class RunLabel(BaseModel):
    run_id: str
    error_present: bool
    error_type: list[FailureModeId]
    root_cause_id: str | None  # shared id for incidents such as a stale snapshot (snowball)
    severity: float  # TODO(stage 4): suite severity score plus dollar error; formula not fixed

    @model_validator(mode="after")
    def _error_present_matches_types(self) -> Self:
        if self.error_present != bool(self.error_type):
            raise ValueError("error_present must be True exactly when error_type is non-empty")
        return self
