"""A small synthetic world for pipeline tests: the fixture household plus households sampled from
a fake SCF table (no real SCF file needed). Prices are the real (synthetic) price layer."""

from functools import cache

import pandas as pd

from advisor_qa.inject.plan import Injector
from advisor_qa.pipeline.agent import Agent
from advisor_qa.pipeline.directory import Directory, build_directory
from advisor_qa.pipeline.params import InjectionParams, PipelineParams, load_pipeline_config
from advisor_qa.pipeline.questions import QuestionGenerator
from advisor_qa.pipeline.tools import World
from advisor_qa.truth.impact import Truth
from advisor_qa.world.households import WorldTables, generate_world
from advisor_qa.world.instruments import load_instruments
from advisor_qa.world.params import HouseholdParams
from advisor_qa.world.price_params import load_price_config
from advisor_qa.world.prices import PriceWorld, build_prices
from advisor_qa.world.scf import eligible_pool
from tests.unit.test_households import fake_scf


@cache
def prices() -> PriceWorld:
    seed, p = load_price_config()
    return build_prices(load_instruments(), p, seed)


@cache
def tables(n: int = 40) -> WorldTables:
    hp = HouseholdParams(foreign_household_rate=0.5, speculative_household_rate=0.5)
    pool = eligible_pool(fake_scf(n_families=80), hp)
    return generate_world(pool, load_instruments(), hp, n, 11)


@cache
def config() -> tuple[int, PipelineParams, InjectionParams]:
    return load_pipeline_config()


class Kit:
    def __init__(self, t: WorldTables | None = None) -> None:
        t = t or tables()
        seed, pipe, inj = config()
        self.seed, self.pipe, self.inj, self.t = seed, pipe, inj, t
        self.world = World(t.households, t.accounts, t.holdings, t.instruments, prices(), seed,
                           pipe.same_day_flow_rate)  # fmt: skip
        self.directory: Directory = build_directory(t.households, seed)
        # several households share a surname, some across advisors (S1 needs a collision)
        for h in self.households()[1:6]:
            first = self.directory.name_of[h].split()[0]
            self.directory.name_of[h] = f"{first} Garcia"
        self.questions = QuestionGenerator(t.accounts, t.holdings, t.instruments, self.directory,
                                           pipe, seed)  # fmt: skip
        self.agent = Agent(self.world, self.directory, pipe)
        self.injector = Injector(self.world, self.directory, inj, seed)
        self.truth = Truth(t.holdings, t.accounts, prices())

    def households(self) -> list[str]:
        return list(pd.Series(self.t.households["household_id"]))
