"""Acceptance tests for the synthetic price layer (docs/price_simulator_spec.md, section 6).

All prices are synthetic. FRED-mode tests use the made-up yield files in tests/fixtures/fred/,
never the real downloads.
"""

from datetime import date

import numpy as np
import pytest

from advisor_qa.world.market_calendar import (
    STRESS,
    TEST,
    TRAIN,
    VAL,
    assign_split,
    build_calendar,
)
from advisor_qa.world.price_params import PriceParams, load_price_config


@pytest.fixture(scope="module")
def cfg() -> tuple[int, PriceParams]:
    return load_price_config()


# --- Calendar and regimes (test 8) -------------------------------------------------------------


def test_calendar_dates(cfg: tuple[int, PriceParams]) -> None:
    seed, p = cfg
    cal = build_calendar(p, seed)
    assert len(cal.days) == 180
    assert cal.days[0] == date(2026, 1, 2) and cal.days[-1] == date(2026, 9, 21)
    assert cal.days[45] == date(2026, 3, 10) and cal.fixture_index == 45  # trading day 46
    assert cal.days[44] == date(2026, 3, 9)
    assert cal.pre_day == date(2025, 12, 31)
    assert not set(p.holidays) & set(cal.days)
    assert all(d.weekday() < 5 for d in cal.days)


def test_split_is_contiguous_and_fixture_in_train(cfg: tuple[int, PriceParams]) -> None:
    seed, p = cfg
    cal = build_calendar(p, seed)
    assert cal.split == assign_split(180, 0.2, 0.2)
    assert cal.split.count(TEST) == 36 and cal.split.count(VAL) == 36
    assert cal.split[-36:] == [TEST] * 36 and cal.split[108:144] == [VAL] * 36
    assert cal.split[cal.fixture_index] == TRAIN


def test_regime_share_and_stress_days_per_split(cfg: tuple[int, PriceParams]) -> None:
    seed, p = cfg
    frame = build_calendar(p, seed).frame()
    stress = frame["regime"] == STRESS
    assert 0.08 <= stress.mean() <= 0.15
    for name in (TRAIN, VAL, TEST):
        assert stress[frame["split"] == name].sum() >= 5
    assert frame.loc[frame["is_fixture_day"], "regime"].tolist() == [STRESS]
    assert frame["is_fixture_day"].sum() == 1


def test_regime_resampling_raises_when_impossible(cfg: tuple[int, PriceParams]) -> None:
    seed, p = cfg
    impossible = p.model_copy(update={"min_stress_days_per_split": 40, "max_regime_tries": 3})
    with pytest.raises(RuntimeError, match="no regime path"):
        build_calendar(impossible, seed)


def test_regime_path_is_seeded(cfg: tuple[int, PriceParams]) -> None:
    seed, p = cfg
    a, b = build_calendar(p, seed), build_calendar(p, seed)
    assert np.array_equal(a.stress, b.stress) and a.regime_seed == b.regime_seed
