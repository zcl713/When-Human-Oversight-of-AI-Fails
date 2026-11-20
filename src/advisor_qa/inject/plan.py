"""Which failure modes hit which run, and the hidden label for each run.

Rates and the severity formula are PLACEHOLDERS (docs/open_questions.md):
- each exposed mode is drawn independently at `rate_scale x rate_by_likelihood[likelihood]`
- D5 (stale snapshot) is an incident, not a per-run draw: on some days every run inside a
  `d5_incident_hours` window uses the same stale snapshot and shares a root_cause_id; the day
  probability is set so about the same share of runs is hit as the likelihood rate gives
- exclusive modes: S2 rewrites the question to another advisor's household and S1 swaps the
  household, so they run alone; I3 skips the calculation, so only S4 can join it; O1 needs a data
  mode on the same run
- severity = highest suite severity among the run's modes + log10(1 + dollar error), where the
  dollar error is |first dollar figure in the answer - true impact of the question as asked|
  (rounding to whole dollars is not an error). Clean runs have severity 0.
"""

import math
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from advisor_qa.inject.modes import DATA, MODE_BY_ID, MODES, Ctx, s2_target
from advisor_qa.pipeline.agent import Faults, RunResult
from advisor_qa.pipeline.directory import Directory
from advisor_qa.pipeline.params import InjectionParams
from advisor_qa.pipeline.questions import Question
from advisor_qa.pipeline.tools import World
from advisor_qa.schema.enums import FailureModeId
from advisor_qa.schema.labels import RunLabel
from advisor_qa.truth.impact import Truth, window_start
from advisor_qa.world.params import REPO_ROOT
from advisor_qa.world.streams import stream

CATALOGUE_CSV = REPO_ROOT / "data" / "reference" / "test_catalogue.csv"
TRADING_MINUTES = 6.5 * 60


def load_catalogue(path: Path = CATALOGUE_CSV) -> pd.DataFrame:
    return pd.read_csv(path).set_index("test_id")


@dataclass(frozen=True)
class Planned:
    question: Question  # possibly rewritten (S2)
    modes: list[str]  # in catalogue order
    faults: Faults
    root_cause_id: str | None


def d5_incidents(
    days: list[date], seed: int, run_rate: float, hours: float
) -> dict[date, tuple[datetime, datetime, str]]:
    """Day -> (start, end, root_cause_id) of that day's stale-snapshot incident."""
    day_p = min(1.0, run_rate * TRADING_MINUTES / (hours * 60))
    out: dict[date, tuple[datetime, datetime, str]] = {}
    span = int(TRADING_MINUTES - hours * 60) // 5
    for d in days:
        rng = stream(seed, "d5_incident", d)
        hit, k = rng.random(), int(rng.integers(span + 1))
        if hit < day_p:
            start = datetime.fromisoformat(f"{d}T09:30:00") + timedelta(minutes=5 * k)
            out[d] = (start, start + timedelta(hours=hours), f"D5-{d.isoformat()}")
    return out


class Injector:
    def __init__(
        self,
        world: World,
        directory: Directory,
        params: InjectionParams,
        seed: int,
        catalogue: pd.DataFrame | None = None,
    ) -> None:
        self.w, self.dir, self.p, self.seed = world, directory, params, seed
        self.cat = load_catalogue() if catalogue is None else catalogue
        likelihood = self.cat["likelihood"].astype(int).to_dict()
        self.rate = {
            m: params.rate_scale * params.rate_by_likelihood[likelihood[m]] for m in MODE_BY_ID
        }
        self.incidents = d5_incidents(
            world.prices.calendar.days, seed, self.rate["D5"], params.d5_incident_hours
        )

    def exposure(self, q: Question, d5_incident: str | None = None) -> list[str]:
        """Modes that could hit this question (O1 counted as if a data mode were drawn)."""
        ctx = Ctx(q, self.w, self.dir, chosen=sorted(DATA), d5_incident=d5_incident)
        return [m.id for m in MODES if m.exposed(ctx)]

    def _incident(self, ts: datetime) -> str | None:
        inc = self.incidents.get(ts.date())
        return inc[2] if inc and inc[0] <= ts < inc[1] else None

    def plan(self, q: Question) -> Planned:
        if q.pinned_clean:
            return Planned(q, [], Faults(), None)
        rng = stream(self.seed, "inject", q.run_id)
        ctx = Ctx(q, self.w, self.dir, d5_incident=self._incident(q.timestamp))
        for m in MODES:
            draw = rng.random()  # always draw, so one mode's exposure never shifts the others
            if not m.exposed(ctx):
                continue
            if m.id == "D5" or draw < self.rate[m.id]:
                ctx.chosen.append(m.id)
        return self.build(q, ctx.chosen, ctx.d5_incident, rng)

    def build(
        self,
        q: Question,
        chosen: list[str],
        d5_incident: str | None = None,
        rng: np.random.Generator | None = None,
    ) -> Planned:
        """Resolve conflicts between drawn modes and configure their faults."""
        rng = rng if rng is not None else stream(self.seed, "inject", q.run_id)
        chosen = list(chosen)
        if "S2" in chosen:
            chosen = ["S2"]
        elif "S1" in chosen:
            chosen = ["S1"]
        elif "I3" in chosen:
            chosen = [m for m in chosen if m in ("I3", "S4")]
        if "O1" in chosen and not DATA & set(chosen):
            chosen.remove("O1")
        if chosen == ["S2"]:
            target = s2_target(Ctx(q, self.w, self.dir), rng)
            types = tuple(self.w.account_table(target)["type"])
            q = replace(
                q, kind="impact_all", household_id=target, prompt="How is this client doing today?",
                intent="portfolio_impact", scope_types=types, window="intraday", benchmark=None,
                product=None, names_client=False,
            )  # fmt: skip
        ctx = Ctx(q, self.w, self.dir, chosen=list(chosen), d5_incident=d5_incident)
        faults = Faults()
        for m in chosen:
            MODE_BY_ID[m].configure(ctx, faults, rng)
        root = d5_incident if "D5" in chosen else None
        return Planned(q, chosen, faults, root)


def dollar_error(q: Question, result: RunResult, truth: Truth) -> float:
    scope = [a for a in truth.all_accounts(q.household_id)
             if truth.accounts(q.household_id).set_index("account_id").loc[a, "type"]
             in q.scope_types]  # fmt: skip
    since = window_start(q.window, q.timestamp, truth.prices.calendar.days)
    true = truth.impact(q.household_id, q.timestamp, scope, since).dollars
    claimed = result.claimed_dollars if result.claimed_dollars is not None else 0.0
    return round(max(0.0, abs(claimed - true) - 0.5), 2)


def make_label(p: Planned, result: RunResult, truth: Truth, catalogue: pd.DataFrame) -> RunLabel:
    err = dollar_error(p.question, result, truth)
    if not p.modes:
        severity = 0.0
    else:
        sev = catalogue["severity"].astype(int).to_dict()
        suite = max(sev[m] for m in p.modes)
        severity = round(suite + math.log10(1 + err), 4)
    return RunLabel(
        run_id=p.question.run_id,
        error_present=bool(p.modes),
        error_type=[FailureModeId(m) for m in p.modes],
        root_cause_id=p.root_cause_id,
        severity=severity,
    )
