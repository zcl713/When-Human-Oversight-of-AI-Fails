"""v1 risk score: a weighted sum of check flags (decisions.md, 2026-11-13). Swappable.

Default weights: the suite severity of the catalogue test a check is mapped to (1-5), and 1 for
supporting checks. The catalogue is reference data, not labels.
"""

from collections.abc import Mapping
from typing import Protocol

import pandas as pd

from advisor_qa.checks.rules import CHECKS
from advisor_qa.world.params import REPO_ROOT

CATALOGUE_CSV = REPO_ROOT / "data" / "reference" / "test_catalogue.csv"


class Scorer(Protocol):
    def score(self, flags: Mapping[str, bool]) -> float: ...


class WeightedSum:
    def __init__(self, weights: Mapping[str, float]) -> None:
        unknown = set(weights) - set(CHECKS)
        if unknown:
            raise ValueError(f"weights for unknown checks {sorted(unknown)}")
        self.weights = dict(weights)

    def score(self, flags: Mapping[str, bool]) -> float:
        return float(sum(w for name, w in self.weights.items() if flags.get(name)))

    @classmethod
    def from_catalogue(cls, path: str | None = None) -> "WeightedSum":
        cat = pd.read_csv(path or CATALOGUE_CSV).set_index("test_id")
        sev = cat["severity"].astype(float).to_dict()
        return cls({name: sev[t] if t else 1.0 for name, (_, t) in CHECKS.items()})


class FlagCount:
    """Unweighted baseline: number of checks that fire."""

    def score(self, flags: Mapping[str, bool]) -> float:
        return float(sum(bool(v) for v in flags.values()))
