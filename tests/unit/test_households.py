import numpy as np
import pandas as pd
import pytest

from advisor_qa.world.checks import check_holdings_sum, check_integrity, etf_share_realised
from advisor_qa.world.households import draw_records, generate_world
from advisor_qa.world.instruments import load_instruments
from advisor_qa.world.params import HouseholdParams
from advisor_qa.world.scf import MODELLED, SCF_COLUMNS, eligible_pool


def fake_scf(n_families: int = 60, seed: int = 0) -> pd.DataFrame:
    """Small SCF-shaped table: 5 records per family, so tests do not need the real file."""
    rng = np.random.default_rng(seed)
    rows = []
    for fam in range(1, n_families + 1):
        base = {c: 0.0 for c in SCF_COLUMNS}
        base.update(
            YY1=fam, AGE=float(rng.integers(30, 80)),
            STOCKS=float(rng.integers(0, 400) * 1000), STMUTF=float(rng.integers(0, 200) * 1000),
            GBMUTF=float(rng.integers(0, 80) * 1000), TFBMUTF=float(rng.integers(0, 50) * 1000),
            CDS=float(rng.integers(0, 50) * 1000), LIQ=float(rng.integers(5, 150) * 1000),
            IRAKH=float(rng.integers(0, 500) * 1000), THRIFT=float(rng.integers(0, 400) * 1000),
            TRUSTS=float(rng.integers(0, 2) * 300_000),
        )  # fmt: skip
        base["RETQLIQ"] = base["IRAKH"] + base["THRIFT"]
        base["RETEQ"] = base["RETQLIQ"] * 0.6
        for imp in range(1, 6):
            rows.append({**base, "Y1": fam * 10 + imp, "WGT": float(rng.integers(500, 20_000))})
    df = pd.DataFrame(rows)
    df["NMMF"] = df[["STMUTF", "GBMUTF", "TFBMUTF"]].sum(axis=1)
    df["FIN"] = df[MODELLED].sum(axis=1)
    return df


def build(seed: int = 7, n: int = 30, **overrides: object):  # type: ignore[no-untyped-def]
    params = HouseholdParams(**overrides)  # type: ignore[arg-type]
    pool = eligible_pool(fake_scf(), params)
    return generate_world(pool, load_instruments(), params, n, seed), pool


def test_integrity_and_holdings_sum() -> None:
    world, _ = build()

    assert check_integrity(world) == []
    assert check_holdings_sum(world) == []
    assert len(world.households) == 30 and "HH-004217" in set(world.households["household_id"])


def test_same_seed_same_tables_and_different_seed_differs() -> None:
    a, _ = build(seed=1)
    b, _ = build(seed=1)
    c, _ = build(seed=2)

    pd.testing.assert_frame_equal(a.holdings, b.holdings)
    pd.testing.assert_frame_equal(a.households, b.households)
    assert not a.holdings.equals(c.holdings)


def test_no_family_twice_when_unique() -> None:
    pool = eligible_pool(fake_scf(), HouseholdParams())

    drawn = draw_records(pool, 40, np.random.default_rng(0), unique_families=True)

    assert drawn["YY1"].is_unique
    with pytest.raises(ValueError):
        draw_records(pool, 10_000, np.random.default_rng(0), unique_families=True)


@pytest.mark.parametrize("share", [0.0, 1.0])
def test_etf_share_extremes(share: float) -> None:
    world, _ = build(n=40, etf_share_of_fund_dollars=share, speculative_household_rate=0.0)

    assert etf_share_realised(world) == pytest.approx(share)


def test_held_away_account_and_ids() -> None:
    world, _ = build(n=40)
    sampled = world.accounts[~world.accounts["household_id"].eq("HH-004217")]

    assert sampled["in_crm"].all()
    assert set(sampled.loc[sampled["held_away"], "type"]) <= {"401k_held_away"}
    assert not (set(sampled["account_id"]) & {"ACC-88120", "ACC-88121", "ACC-88122"})


def test_held_away_can_be_missing_from_crm() -> None:
    world, _ = build(n=40, held_away_not_in_crm_rate=1.0)
    ha = world.accounts[
        world.accounts["held_away"] & (world.accounts["household_id"] != "HH-004217")
    ]

    assert len(ha) > 0 and not ha["in_crm"].any()


@pytest.mark.parametrize("mf_only", [True, False])
def test_held_away_mutual_funds_only(mf_only: bool) -> None:
    world, _ = build(n=55, etf_share_of_fund_dollars=0.8, held_away_mutual_funds_only=mf_only)
    is_mf = world.instruments.set_index("instrument_id")["is_mutual_fund"]
    away = world.accounts["held_away"] & (world.accounts["household_id"] != "HH-004217")
    held = world.holdings[world.holdings["account_id"].isin(world.accounts[away]["account_id"])]
    all_mf = bool(is_mf[held["instrument_id"]].all())

    assert len(held) > 0
    assert all_mf is mf_only
