"""Generate the runs dataset: questions, agent runs (clean or with injected faults), traces and
hidden labels. ALL DATA IS SYNTHETIC.

`uv run python -m advisor_qa.pipeline.generate --households data/synthetic/households
    --out data/synthetic/runs [--fred-dir data/raw/fred]`

Prices are rebuilt in memory from the config seed (identical to `advisor_qa.world.prices` with the
same seed and --fred-dir). Each trading day draws its questions from the households in the same
split as the day (diagonal split). Output layout: `advisor_qa.store.runs`.
"""

import argparse
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from advisor_qa.inject.plan import Injector, dollar_error, load_catalogue, make_label
from advisor_qa.pipeline.agent import Agent, RunResult
from advisor_qa.pipeline.directory import Directory, build_directory
from advisor_qa.pipeline.params import InjectionParams, PipelineParams, load_pipeline_config
from advisor_qa.pipeline.questions import QuestionGenerator
from advisor_qa.pipeline.tools import World
from advisor_qa.schema.labels import RunLabel
from advisor_qa.schema.trace import RunTrace
from advisor_qa.store.labels import write_labels
from advisor_qa.store.runs import (
    DIRECTORY_FILE,
    SPLIT_FILE,
    SPLITS,
    Manifest,
    write_answers,
    write_manifest,
    write_runs,
)
from advisor_qa.truth.impact import Truth
from advisor_qa.world.instruments import load_instruments
from advisor_qa.world.params import DEFAULT_CONFIG, REPO_ROOT
from advisor_qa.world.price_params import load_price_config
from advisor_qa.world.prices import PriceWorld, build_prices
from advisor_qa.world.split import assign_households, load_split_params


@dataclass
class Dataset:
    runs: dict[str, list[RunTrace]] = field(default_factory=lambda: defaultdict(list))
    answers: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: defaultdict(list))
    labels: dict[str, list[RunLabel]] = field(default_factory=lambda: defaultdict(list))
    clean_errors: list[tuple[str, float]] = field(default_factory=list)  # should stay empty
    split: pd.DataFrame | None = None
    directory: Directory | None = None


def generate(
    households: pd.DataFrame,
    accounts: pd.DataFrame,
    holdings: pd.DataFrame,
    instruments: pd.DataFrame,
    prices: PriceWorld,
    pipe: PipelineParams,
    inj: InjectionParams,
    seed: int,
    split: pd.DataFrame | None = None,
    max_days: int | None = None,
) -> Dataset:
    if split is None:
        split = assign_households(list(households["household_id"]), seed, load_split_params())
    world = World(
        households, accounts, holdings, instruments, prices, seed, pipe.same_day_flow_rate
    )
    directory = build_directory(households, seed)
    questions = QuestionGenerator(accounts, holdings, instruments, directory, pipe, seed)
    agent = Agent(world, directory, pipe)
    catalogue = load_catalogue()
    injector = Injector(world, directory, inj, seed, catalogue)
    truth = Truth(holdings, accounts, prices)
    by_split = split.groupby("split")["household_id"].apply(list).to_dict()
    cal = prices.calendar
    out = Dataset(split=split, directory=directory)
    days = list(zip(cal.days, cal.split, strict=True))[:max_days]
    for i, (d, s) in enumerate(days):
        for q in questions.day(d, by_split[s], fixture_day=(i == cal.fixture_index)):
            planned = injector.plan(q)
            result: RunResult = agent.run(planned.question, planned.faults)
            label = make_label(planned, result, truth, catalogue)
            if not planned.modes:
                err = dollar_error(planned.question, result, truth)
                if err > 0:
                    out.clean_errors.append((q.run_id, err))
            out.runs[s].append(result.trace)
            out.answers[s].append((q.run_id, result.answer))
            out.labels[s].append(label)
    for s in list(out.runs):
        order = sorted(range(len(out.runs[s])),
                       key=lambda k: (out.runs[s][k].timestamp, out.runs[s][k].run_id))  # fmt: skip
        out.runs[s] = [out.runs[s][k] for k in order]
        out.answers[s] = [out.answers[s][k] for k in order]
        out.labels[s] = [out.labels[s][k] for k in order]
    return out


def write_dataset(ds: Dataset, root: Path, seed: int, config_path: Path = DEFAULT_CONFIG) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for s in SPLITS:
        write_runs(root, s, ds.runs.get(s, []))
        write_answers(root, s, ds.answers.get(s, []))
        write_labels(root, s, ds.labels.get(s, []))
    assert ds.split is not None and ds.directory is not None
    ds.split.to_csv(root / SPLIT_FILE, index=False, lineterminator="\n")
    ds.directory.frame().to_csv(root / DIRECTORY_FILE, index=False, lineterminator="\n")
    write_manifest(
        root,
        Manifest(
            seed=seed,
            config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
            counts={s: len(ds.runs.get(s, [])) for s in SPLITS},
            error_counts={s: sum(lb.error_present for lb in ds.labels.get(s, [])) for s in SPLITS},
        ),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--households", type=Path, default=REPO_ROOT / "data/synthetic/households")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data/synthetic/runs")
    ap.add_argument("--fred-dir", type=Path, default=None)
    ap.add_argument("--max-days", type=int, default=None, help="only the first N trading days")
    args = ap.parse_args()

    seed, pipe, inj = load_pipeline_config()
    price_seed, pp = load_price_config()
    h = args.households
    hh, acc, hold = (pd.read_csv(h / f"{n}.csv") for n in ("households", "accounts", "holdings"))
    instruments = load_instruments()
    prices = build_prices(instruments, pp, price_seed, args.fred_dir)
    split = pd.read_csv(h / SPLIT_FILE) if (h / SPLIT_FILE).exists() else None
    ds = generate(hh, acc, hold, instruments, prices, pipe, inj, seed, split, args.max_days)
    if ds.clean_errors:
        raise RuntimeError(f"clean runs disagree with the truth function: {ds.clean_errors[:5]}")
    write_dataset(ds, args.out, seed)
    for s in SPLITS:
        n = len(ds.runs.get(s, []))
        e = sum(lb.error_present for lb in ds.labels.get(s, []))
        print(f"{s}: {n} runs, {e} with an injected error ({e / max(n, 1):.1%})")
    print(f"SYNTHETIC runs written to {args.out} (yields: {prices.yield_source})")


if __name__ == "__main__":
    main()
