"""The instruments reference table (data/reference/instruments.csv) and pool lookups."""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from advisor_qa.world.params import REPO_ROOT

INSTRUMENTS_CSV = REPO_ROOT / "data" / "reference" / "instruments.csv"

COLUMNS = [
    "instrument_id", "archetype", "ticker", "instrument_type", "asset_class", "duration_years",
    "is_mutual_fund", "pricing", "proxy_ticker", "reference_price", "notes",
]  # fmt: skip


def load_instruments(path: Path = INSTRUMENTS_CSV) -> pd.DataFrame:
    df = pd.read_csv(path)
    if list(df.columns) != COLUMNS:
        raise ValueError(f"instruments.csv columns must be {COLUMNS}")
    if df["instrument_id"].duplicated().any():
        raise ValueError("duplicate instrument_id")
    if not set(df["pricing"]) <= {"intraday", "end_of_day_nav"}:
        raise ValueError("pricing must be intraday or end_of_day_nav")
    if (df["is_mutual_fund"] != (df["pricing"] == "end_of_day_nav")).any():
        raise ValueError("mutual funds must be end_of_day_nav and everything else intraday")
    return df


@dataclass(frozen=True)
class Candidates:
    """Instruments a pool of dollars can go into, split by wrapper."""

    etf: tuple[str, ...]
    mf: tuple[str, ...]


def _ids(inst: pd.DataFrame, asset_classes: list[str], mutual_fund: bool) -> tuple[str, ...]:
    sel = inst[inst["asset_class"].isin(asset_classes) & (inst["is_mutual_fund"] == mutual_fund)]
    return tuple(sel["instrument_id"])


def pool_candidates(inst: pd.DataFrame) -> dict[str, Candidates]:
    """Pool name -> candidate instruments. Target-date funds are chosen by age, not listed here."""
    spec = inst[inst["instrument_type"].isin(["options_etf", "leveraged_etf"])]
    pools = {
        "equity": ["us_equity_large", "us_equity_total", "us_equity_dividend", "us_equity_growth"],
        "bond": ["us_bond_intermediate", "us_bond_treasury"],
        "short": ["us_bond_short"],
        "muni": ["us_bond_muni"],
        "cash": ["cash"],
    }
    out = {n: Candidates(_ids(inst, c, False), _ids(inst, c, True)) for n, c in pools.items()}
    out["spec"] = Candidates(tuple(spec["instrument_id"]), ())
    return out


def target_date_id(inst: pd.DataFrame, age: float) -> str:
    """Target-date fund vintage by age: income 63+, 2030 for 58-62, 2040 for 48-57, else 2050."""
    name = (
        "target_date_income" if age >= 63
        else "target_date_2030" if age >= 58
        else "target_date_2040" if age >= 48
        else "target_date_2050"
    )  # fmt: skip
    return str(inst.loc[inst["archetype"] == name, "instrument_id"].iloc[0])
