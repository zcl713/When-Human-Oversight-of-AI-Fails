"""Checks on the household tables: integrity, holdings-sum, and marginals against the SCF pool."""

import numpy as np
import pandas as pd

from advisor_qa.world.households import HELD_AWAY, IRA, TRUST, WorldTables

ACCOUNT_TYPES = {"taxable", IRA, TRUST, HELD_AWAY}


def check_integrity(t: WorldTables) -> list[str]:
    """Returns a list of problems (empty = pass)."""
    problems: list[str] = []
    h, a, x, i = t.households, t.accounts, t.holdings, t.instruments
    if h["household_id"].duplicated().any() or a["account_id"].duplicated().any():
        problems.append("duplicate household_id or account_id")
    if not set(a["type"]) <= ACCOUNT_TYPES:
        problems.append("unknown account type")
    if (a["held_away"] != (a["type"] == HELD_AWAY)).any():
        problems.append("held_away must be true exactly for 401k_held_away accounts")
    if not x["instrument_id"].isin(i["instrument_id"]).all():
        problems.append("holding points to an unknown instrument")
    owner = a.set_index("account_id")["household_id"]
    if not x["account_id"].isin(owner.index).all():
        problems.append("holding points to an unknown account")
    elif (x["account_id"].map(owner) != x["household_id"]).any():
        problems.append("holding's account belongs to a different household")
    if not h["advisor_id"].str.fullmatch(r"ADV-\d{4}").all():
        problems.append("bad advisor_id format")
    if not h["household_id"].str.fullmatch(r"HH-\d{6}").all():
        problems.append("bad household_id format")
    if not a["account_id"].str.fullmatch(r"ACC-\d{5}").all():
        problems.append("bad account_id format")
    return problems


def check_holdings_sum(t: WorldTables, tol_per_holding: float = 0.05) -> list[str]:
    """Each household's holdings sum to total_value, and to the SCF dollars it was built from."""
    problems: list[str] = []
    sums = t.holdings.groupby("household_id")["value_usd"].sum().round(2)
    h = t.households.set_index("household_id")
    if not (sums.reindex(h.index).sub(h["total_value"]).abs() < 0.005).all():
        problems.append("holdings do not sum to total_value")
    n = t.holdings.groupby("household_id").size().reindex(h.index)
    drift = (h["total_value"] - h["scf_modelled_value"]).abs()
    if (drift > tol_per_holding * n).any():
        problems.append("total_value drifts from the SCF dollars beyond rounding")
    return problems


def _wmean(x: pd.Series, w: pd.Series) -> float:
    return float(np.average(x, weights=w))


def marginals(t: WorldTables, pool: pd.DataFrame) -> pd.DataFrame:
    """Sample (unweighted, sampling was by WGT) vs the pool's weighted population. z uses the
    sample's standard error only; replicate weights are not available, so the population side
    carries no error. Sampled households only (fixture excluded)."""
    h = t.households[~t.households["is_fixture"]]
    types = t.accounts[t.accounts["household_id"].isin(h["household_id"])]
    n = len(h)
    rows: list[tuple[str, float, float, float]] = []

    def add_mean(name: str, sample: pd.Series, pop: float) -> None:
        rows.append((name, float(sample.mean()), pop, float(sample.std(ddof=1) / np.sqrt(n))))

    def add_share(name: str, sample_has: float, pop: float) -> None:
        rows.append((name, sample_has, pop, float(np.sqrt(max(pop * (1 - pop), 1e-12) / n))))

    add_mean("age_mean", h["age"], _wmean(pool["AGE"], pool["WGT"]))
    add_mean(
        "log10_total_value_mean",
        np.log10(h["total_value"]),
        _wmean(np.log10(pool["modelled_value"]), pool["WGT"]),
    )
    for scf_col, acct in (("IRAKH", IRA), ("THRIFT", HELD_AWAY), ("TRUSTS", TRUST)):
        has = types[types["type"] == acct]["household_id"].nunique() / n
        add_share(f"share_with_{acct}", has, _wmean((pool[scf_col] > 0).astype(float), pool["WGT"]))
    out = pd.DataFrame(rows, columns=["metric", "sample", "population", "se"])
    out["z"] = (out["sample"] - out["population"]) / out["se"]
    return out


def etf_share_realised(t: WorldTables) -> float:
    """ETF share of dollars in pools that have both an ETF and a mutual-fund option.
    Sampled households only (the hand-built fixture is excluded), and held-away 401(k)s are left
    out because they can be mutual-fund-only (`held_away_mutual_funds_only`)."""
    inst = t.instruments.set_index("instrument_id")
    both = {"us_equity_large", "us_equity_total", "us_equity_dividend", "us_equity_growth",
            "us_bond_intermediate", "us_bond_treasury", "us_bond_short"}  # fmt: skip
    sampled = t.households.loc[~t.households["is_fixture"], "household_id"]
    away = t.accounts.loc[t.accounts["held_away"], "account_id"]
    x = t.holdings[t.holdings["household_id"].isin(sampled) & ~t.holdings["account_id"].isin(away)]
    x = x[x["instrument_id"].map(inst["asset_class"]).isin(both)]
    is_mf = x["instrument_id"].map(inst["is_mutual_fund"]).astype(bool)
    return float(x.loc[~is_mf, "value_usd"].sum() / x["value_usd"].sum())
