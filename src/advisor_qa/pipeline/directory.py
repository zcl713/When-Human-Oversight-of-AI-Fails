"""CRM directory: who advises each household and the client's (synthetic) name.

Names are made-up combinations of common first names and surnames, drawn per household from its
own stream. About 750 households share 80 surnames, so surname-only prompts are often ambiguous;
that is the setup for S1 (client name collision). The full name is unique.
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from advisor_qa.world import fixture as fx
from advisor_qa.world.streams import stream

FIRST = (
    "Alex Jordan Taylor Morgan Casey Riley Jamie Avery Quinn Drew Sam Robin Dana Kim Lee Pat "
    "Maria James Linda Robert Susan David Karen Michael Nancy Thomas Laura Daniel Ellen Paul "
    "Grace Henry Ruth Walter Irene Frank Joan Arthur Helen Victor"
).split()
SURNAMES = (
    "Smith Johnson Williams Brown Jones Garcia Miller Davis Rodriguez Martinez Hernandez Lopez "
    "Gonzalez Wilson Anderson Thomas Taylor Moore Jackson Martin Lee Perez Thompson White Harris "
    "Sanchez Clark Ramirez Lewis Robinson Walker Young Allen King Wright Scott Torres Nguyen Hill "
    "Flores Green Adams Nelson Baker Hall Rivera Campbell Mitchell Carter Roberts Gomez Phillips "
    "Evans Turner Diaz Parker Cruz Edwards Collins Reyes Stewart Morris Morales Murphy Cook "
    "Rogers Gutierrez Ortiz Morgan Cooper Peterson Bailey Reed Kelly Howard Ramos Kim Cox Ward"
).split()
FIXTURE_NAME = "Avery Chen"
DIRECTORY_COLUMNS = ["household_id", "advisor_id", "client_name"]


@dataclass(frozen=True)
class Directory:
    advisor_of: dict[str, str]
    name_of: dict[str, str]

    @property
    def by_surname(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for hh, name in sorted(self.name_of.items()):
            out[surname(name)].append(hh)
        return dict(out)

    def frame(self) -> pd.DataFrame:
        ids = sorted(self.advisor_of)
        return pd.DataFrame(
            {
                "household_id": ids,
                "advisor_id": [self.advisor_of[h] for h in ids],
                "client_name": [self.name_of[h] for h in ids],
            }
        )

    def write(self, path: Path) -> None:
        self.frame().to_csv(path, index=False)

    @classmethod
    def read(cls, path: Path) -> "Directory":
        df = pd.read_csv(path)
        return cls(
            dict(zip(df["household_id"], df["advisor_id"], strict=True)),
            dict(zip(df["household_id"], df["client_name"], strict=True)),
        )


def surname(full_name: str) -> str:
    return full_name.split()[-1]


def build_directory(households: pd.DataFrame, seed: int) -> Directory:
    advisor_of = dict(zip(households["household_id"], households["advisor_id"], strict=True))
    used: set[str] = set()
    name_of: dict[str, str] = {}
    for hh in sorted(advisor_of):
        if hh == fx.FIXTURE_HOUSEHOLD_ID:
            name = FIXTURE_NAME
        else:
            rng = stream(seed, "client_name", hh)
            while True:
                name = f"{FIRST[int(rng.integers(len(FIRST)))]} "
                name += SURNAMES[int(rng.integers(len(SURNAMES)))]
                if name not in used and name != FIXTURE_NAME:
                    break
        used.add(name)
        name_of[hh] = name
    return Directory(advisor_of, name_of)
