"""Run-generator and injection parameters (`pipeline:` and `injection:` in config/default.yaml)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

from advisor_qa.world.params import DEFAULT_CONFIG


class PipelineParams(BaseModel):
    questions_per_day: int = 60
    question_mix: dict[str, float]
    first_question_time: str = "09:35"
    surname_prompt_share: float = 0.2
    same_day_flow_rate: float = 0.05
    tools: list[str]
    calc_tools: dict[str, str]  # "intraday" / "period" -> calculation tool
    expected_tools: dict[str, list[str]]  # "core" plus resolved intent -> extra tools

    @model_validator(mode="after")
    def _check(self) -> "PipelineParams":
        if abs(sum(self.question_mix.values()) - 1) > 1e-9:
            raise ValueError("pipeline.question_mix must sum to 1")
        named = {"calc_tools": list(self.calc_tools.values()), **self.expected_tools}
        for intent, tools in named.items():
            unknown = set(tools) - set(self.tools)
            if unknown:
                raise ValueError(f"expected_tools[{intent}] names unknown tools {sorted(unknown)}")
        return self


class InjectionParams(BaseModel):
    rate_by_likelihood: dict[int, float]
    rate_scale: float = 1.0
    d5_incident_hours: float = 2.0


def load_pipeline_config(
    path: Path = DEFAULT_CONFIG,
) -> tuple[int, PipelineParams, InjectionParams]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    pipe = PipelineParams(questions_per_day=raw.get("questions_per_day", 60), **raw["pipeline"])
    return int(raw["seed"]), pipe, InjectionParams(**raw["injection"])
