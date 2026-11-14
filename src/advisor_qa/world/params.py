"""Household-layer parameters, read from the `households` section of config/default.yaml."""

from pathlib import Path

import yaml
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "config" / "default.yaml"


class HouseholdParams(BaseModel):
    etf_share_of_fund_dollars: float = 0.5
    n_advisors: int = 25
    band_min_fin: float = 250_000
    band_max_fin: float = 5_000_000
    min_modelled_value: float = 100_000
    min_holding_value: float = 5_000
    unique_families: bool = True
    speculative_household_rate: float = 0.10
    speculative_max_equity_share: float = 0.03
    target_date_share_of_retirement: float = 0.4
    trust_equity_share: float = 0.6
    held_away_not_in_crm_rate: float = 0.0


def load_config(path: Path = DEFAULT_CONFIG) -> tuple[int, HouseholdParams]:
    """Returns (seed, household params)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return int(raw["seed"]), HouseholdParams(**raw.get("households", {}))
