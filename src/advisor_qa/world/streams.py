"""Independent random streams keyed by purpose (and instrument, date, ...).

Each draw comes from its own `SeedSequence([seed, purpose, *keys])`, so adding an instrument or a
day never changes the numbers drawn for the others.
"""

import zlib
from datetime import date

import numpy as np

Key = int | str | date


def _word(k: Key) -> int:
    if isinstance(k, date):
        return k.toordinal()
    if isinstance(k, int):
        if k < 0:
            raise ValueError("stream keys must be non-negative")
        return k
    return zlib.crc32(k.encode("utf-8"))


def stream(seed: int, purpose: str, *keys: Key) -> np.random.Generator:
    return np.random.default_rng(
        np.random.SeedSequence([_word(seed), _word(purpose)] + [_word(k) for k in keys])
    )
