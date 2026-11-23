"""Question generator: what advisors ask, across intents, including ambiguous ones.

Each question records what it really asks (true intent, account scope, window, benchmark), so the
truth function can score any answer. Questions fall on the 5-minute grid. Households are drawn
from the split matching the day's date split (diagonal split, decisions.md 2026-11-20).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd

from advisor_qa.pipeline.directory import Directory, surname
from advisor_qa.pipeline.params import PipelineParams
from advisor_qa.world import fixture as fx
from advisor_qa.world.streams import stream

ACCOUNT_LABEL = {
    "taxable": "taxable account",
    "ira": "IRA",
    "trust": "trust",
    "401k_held_away": "401(k)",
}
WINDOW_PHRASE = {"wtd": "this week", "mtd": "this month", "ytd": "this year"}
BENCHMARK_PHRASE = {"SPY": "the S&P 500", "VTI": "the total market", "BND": "the bond market"}
INTENT_OF_KIND = {
    "impact_all": "portfolio_impact",
    "impact_scoped": "portfolio_impact",
    "period": "portfolio_impact",
    "ambiguous_impact": "portfolio_impact",
    "ambiguous_window": "portfolio_impact",
    "followup": "portfolio_impact",
    "benchmark": "benchmark_comparison",
    "worried": "market_concern",
    "sell": "sell_inquiry",
    "tax": "tax_inquiry",
    "rates": "rates_impact",
    "inception": "portfolio_performance",
}
AMBIGUOUS_KINDS = {"ambiguous_impact", "ambiguous_window", "worried"}
FIXTURE_PROMPT = "How much has my portfolio lost today?"
WORKBOOK_PROMPT = "What's the impact on my portfolio from today's market?"  # client test suite
MIX = "MIX"  # benchmark not named ("vs. the market"): the right one depends on the client's mix


@dataclass(frozen=True)
class Question:
    run_id: str
    kind: str
    household_id: str
    advisor_id: str
    timestamp: datetime
    prompt: str
    turn: int
    intent: str  # true intent
    scope_types: tuple[str, ...]  # account types truly asked about
    window: str  # intraday, wtd, mtd, ytd
    benchmark: str | None  # ticker the prompt names, MIX for "the market", None if not asked
    product: str | None  # instrument_id named in a sell question
    names_client: bool  # the prompt names the client by surname
    pinned_clean: bool = False  # the fixture run: never injected
    prior_scope: tuple[str, ...] = ()  # follow-ups: the scope set in the earlier turn


def run_id_for(seed: int, day: date, k: int) -> str:
    rng = stream(seed, "run_id", day, k)
    return "".join(f"{int(x):08x}" for x in rng.integers(0, 2**32, size=4))


def _grid(first: str, last: str = "16:00", step: int = 5) -> list[str]:
    h, m = map(int, first.split(":"))
    lh, lm = map(int, last.split(":"))
    out, t = [], h * 60 + m
    while t <= lh * 60 + lm:
        out.append(f"{t // 60:02d}:{t % 60:02d}")
        t += step
    return out


def _pick(rng: np.random.Generator, options: Sequence[str]) -> str:
    return options[int(rng.integers(len(options)))]


class QuestionGenerator:
    def __init__(
        self,
        accounts: pd.DataFrame,
        holdings: pd.DataFrame,
        instruments: pd.DataFrame,
        directory: Directory,
        params: PipelineParams,
        seed: int,
    ) -> None:
        self.seed, self.p, self.dir = seed, params, directory
        self.types = {
            str(h): list(g["type"]) for h, g in accounts.groupby("household_id", sort=True)
        }
        inst = instruments.set_index("instrument_id")
        eq = holdings[
            holdings["instrument_id"]
            .map(inst["instrument_type"])
            .isin(["equity_etf", "mutual_fund", "leveraged_etf", "options_etf", "foreign_equity"])
        ]
        top = eq.sort_values(["household_id", "value_usd"], ascending=[True, False])
        self.top_equity = dict(top.groupby("household_id")["instrument_id"].first())
        self.ticker = dict(inst["ticker"])
        self.times = _grid(params.first_question_time)
        self.kinds = list(params.question_mix)
        w = np.array([params.question_mix[k] for k in self.kinds])
        self.kind_p = w / w.sum()

    def day(self, d: date, households: Sequence[str], fixture_day: bool) -> list[Question]:
        hh = sorted(households)
        out: list[Question] = []
        for k in range(self.p.questions_per_day):
            if fixture_day and k == 0:
                out.append(self.fixture(d))
                continue
            rng = stream(self.seed, "question", d, k)
            h = hh[int(rng.integers(len(hh)))]
            kind = self.kinds[int(rng.choice(len(self.kinds), p=self.kind_p))]
            t = _pick(rng, self.times)
            out.append(self._question(rng, run_id_for(self.seed, d, k), kind, h, d, t))
        return out

    def fixture(self, d: date) -> Question:
        types = tuple(self.types[fx.FIXTURE_HOUSEHOLD_ID])
        return Question(
            run_id_for(self.seed, d, 0), "impact_all", fx.FIXTURE_HOUSEHOLD_ID,
            fx.FIXTURE_ADVISOR_ID, datetime.fromisoformat(f"{d}T14:05:00"), FIXTURE_PROMPT, 1,
            "portfolio_impact", types, "intraday", None, None, False, pinned_clean=True,
        )  # fmt: skip

    def _question(
        self, rng: np.random.Generator, run_id: str, kind: str, h: str, d: date, t: str
    ) -> Question:
        types = self.types[h]
        names = rng.random() < self.p.surname_prompt_share
        client = f"the {surname(self.dir.name_of[h])} household" if names else "this client"
        scope, window, bench, product, turn = tuple(types), "intraday", None, None, 1
        prior: tuple[str, ...] = ()
        if kind in ("impact_scoped", "followup") and len(types) < 2:
            kind = "impact_all"  # nothing to scope
        if kind == "impact_all":
            prompt = _pick(rng, [
                FIXTURE_PROMPT,
                f"How is {client} doing today?",
                f"How much is {client} up or down today across all accounts?",
                f"What's today's move for {client}?",
            ])  # fmt: skip
        elif kind == "impact_scoped":
            t_ = _pick(rng, types)
            if rng.random() < 0.5:
                scope = (t_,)
                prompt = f"How is {client}'s {ACCOUNT_LABEL[t_]} doing today?"
            else:
                scope = tuple(x for x in types if x != t_)
                prompt = f"How much is {client} down today, excluding the {ACCOUNT_LABEL[t_]}?"
        elif kind == "period":
            window = _pick(rng, ["wtd", "mtd", "ytd"])
            prompt = f"How has {client} done {WINDOW_PHRASE[window]}?"
        elif kind == "benchmark":
            bench = _pick(rng, [MIX, MIX, "SPY", "VTI", "BND"])
            if bench == MIX:  # the workbook's I4 prompt
                prompt = f"How did {client} do versus the market today?"
            else:
                prompt = f"How did {client} do versus {BENCHMARK_PHRASE[bench]} today?"
        elif kind == "ambiguous_impact":
            prompt = _pick(rng, [
                WORKBOOK_PROMPT,
                f"What's the impact on {client}'s portfolio?",
                f"What's the impact for {client}?",
            ])  # fmt: skip
        elif kind == "ambiguous_window":
            prompt = _pick(
                rng, [f"How did we do for {client}?", f"How are we doing with {client}?"]
            )
        elif kind == "worried":
            prompt = _pick(rng, [
                f"Should {client} be worried?",
                f"Is this market something {client} should be worried about?",
            ])  # fmt: skip
        elif kind == "sell":
            product = self.top_equity.get(h)
            if product is None:
                kind = "worried"
                prompt = f"Should {client} be worried?"
            else:
                prompt = f"Should we sell {client}'s {self.ticker[product]} after today's drop?"
        elif kind == "tax":
            prompt = f"Can {client} use today's loss for taxes?"
        elif kind == "rates":  # C7 setup: the yield move is reported in basis points
            prompt = f"What does the rate move mean for {client}?"
        elif kind == "inception":  # O4 setup: history before 2019 is missing
            window = "ytd"
            prompt = f"What's {client}'s return since they started?"
        else:  # followup (I6): turn 1 was scoped to one account; turn 3 asks for the total
            t_ = _pick(rng, types)
            prior, turn = (t_,), 3
            prompt = "And the total?"
            names = False
        names = names and client in prompt  # some templates do not name the client
        ts = datetime.fromisoformat(f"{d}T{t}:00")
        intent = INTENT_OF_KIND[kind]
        adv = self.dir.advisor_of[h]
        return Question(
            run_id,
            kind,
            h,
            adv,
            ts,
            prompt,
            turn,
            intent,
            scope,
            window,
            bench,
            product,
            names,
            prior_scope=prior,
        )
