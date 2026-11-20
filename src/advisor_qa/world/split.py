"""Household train / val / test split (decisions.md, 2026-11-20).

Each household gets a seeded uniform draw from its own stream (keyed by household_id, so adding a
household never moves the others); households are ranked by it and the lowest val+test fraction
become test, then val. Pinned households (the fixture, in train) are placed first and count
toward their split. The run generator pairs this with the date split diagonally: a run on a test
date draws only test households.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

import pandas as pd
import yaml
from pydantic import BaseModel, model_validator

from advisor_qa.world import fixture as fx
from advisor_qa.world.market_calendar import TEST, TRAIN, VAL
from advisor_qa.world.params import DEFAULT_CONFIG
from advisor_qa.world.streams import stream

SPLIT_FILE = "split_households.csv"


class SplitParams(BaseModel):
    val_household_fraction: float = 0.2
    test_household_fraction: float = 0.2

    @model_validator(mode="after")
    def _check(self) -> "SplitParams":
        if self.val_household_fraction + self.test_household_fraction >= 1:
            raise ValueError("val + test household fractions must be below 1")
        return self


def load_split_params(path: Path = DEFAULT_CONFIG) -> SplitParams:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")).get("split", {})
    keys = SplitParams.model_fields
    return SplitParams(**{k: v for k, v in raw.items() if k in keys})


def assign_households(
    household_ids: Sequence[str],
    seed: int,
    p: SplitParams,
    pinned: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """DataFrame(household_id, split), sorted by household_id."""
    pinned = dict({fx.FIXTURE_HOUSEHOLD_ID: TRAIN} if pinned is None else pinned)
    ids = sorted(set(household_ids))
    n = len(ids)
    want = {TEST: round(n * p.test_household_fraction), VAL: round(n * p.val_household_fraction)}
    split: dict[str, str] = {h: s for h, s in pinned.items() if h in set(ids)}
    for s in (TEST, VAL):
        want[s] -= sum(1 for v in split.values() if v == s)
    free = sorted(
        (h for h in ids if h not in split),
        key=lambda h: (float(stream(seed, "household_split", h).random()), h),
    )
    k = 0
    for s in (TEST, VAL):
        for h in free[k : k + max(want[s], 0)]:
            split[h] = s
        k += max(want[s], 0)
    for h in free[k:]:
        split[h] = TRAIN
    return pd.DataFrame({"household_id": ids, "split": [split[h] for h in ids]})


def read_split(path: Path) -> dict[str, str]:
    df = pd.read_csv(path)
    return dict(zip(df["household_id"].astype(str), df["split"].astype(str), strict=True))
