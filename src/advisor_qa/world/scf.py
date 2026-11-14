"""Load the 2025 SCF Summary Extract and select the eligible record pool.

Source: Board of Governors of the Federal Reserve System (2026), Survey of Consumer Finances,
DOI 10.17016/8799.1. The extract has 5 records (implicates) per family; `WGT` is per record and the
sum over all 21,800 records is the US family population.
"""

from pathlib import Path

import pandas as pd

from advisor_qa.world.params import HouseholdParams

SCF_COLUMNS = [
    "YY1", "Y1", "WGT", "AGE", "FIN",
    "STOCKS", "STMUTF", "TFBMUTF", "GBMUTF", "OBMUTF", "COMUTF", "OMUTF", "NMMF",
    "BOND", "CDS", "LIQ", "IRAKH", "THRIFT", "TRUSTS", "RETEQ", "RETQLIQ",
]  # fmt: skip

# SCF categories we turn into holdings. FIN also contains savings bonds, cash-value life insurance,
# annuities and other financial assets, which we do not model.
MODELLED = ["STOCKS", "NMMF", "BOND", "CDS", "LIQ", "IRAKH", "THRIFT", "TRUSTS"]


def load_scf(xlsx_path: Path, cache_path: Path) -> pd.DataFrame:
    """Reads the extract (CSV cache in the interim data; the .xlsx takes about a minute)."""
    if cache_path.exists():
        return pd.read_csv(cache_path)
    df = pd.read_excel(xlsx_path, usecols=SCF_COLUMNS)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False)
    return df


def eligible_pool(scf: pd.DataFrame, params: HouseholdParams) -> pd.DataFrame:
    """Records inside the FIN wealth band whose modelled holdings clear the minimum."""
    pool = scf.copy()
    pool["modelled_value"] = pool[MODELLED].sum(axis=1)
    keep = pool["FIN"].between(params.band_min_fin, params.band_max_fin)
    keep &= pool["modelled_value"] >= params.min_modelled_value
    return pool[keep].reset_index(drop=True)
