"""The latent (true) price table, prices_latent.csv. SYNTHETIC PRICES.

For the truth function and the error injectors ONLY. Checks and scoring must never import this
module, read the latent file or call `PriceWorld.latent_value`; tests/unit/test_prices.py fails if
any module under advisor_qa.checks or advisor_qa.scoring does.
"""

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from advisor_qa.world.prices import PriceWorld

LATENT_FILE = "prices_latent.csv"
LATENT_COLUMNS = ["instrument_id", "market_date", "time", "value"]


def latent_frame(world: "PriceWorld") -> pd.DataFrame:
    """One row per series, trading day and grid time: the true value."""
    dates, times = world.grid_keys()
    parts = [
        pd.DataFrame(
            {
                "instrument_id": s.series_id,
                "market_date": dates,
                "time": times,
                "value": np.asarray(world.paths[s.series_id][1:]).ravel(),
            }
        )
        for s in world.series
    ]
    return pd.concat(parts, ignore_index=True)[LATENT_COLUMNS]
