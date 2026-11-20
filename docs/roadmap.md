# Roadmap: what is done, what is left, who does what

Last updated 2026-11-20. Decisions behind the finished work are in `decisions.md`; unresolved
questions are in `open_questions.md`.

## Status

| Piece | Where | Status |
|---|---|---|
| Trace schema (39 fields, 7 groups) and hidden `RunLabel` | `src/advisor_qa/schema/` | Done |
| Test catalogue (30 failure modes D/C/I/O/S, scores) | `data/reference/test_catalogue.csv` | **Done** (scores; `mapped_mutation` and `mapped_check` filled) |
| Households, accounts, holdings, instruments | `src/advisor_qa/world/`, `data/reference/instruments.csv` | **Done** (SCF sampler, fixture HH-004217, checks, build command) |
| Calendar, market days, prices | `src/advisor_qa/world/prices.py`, `config/default.yaml` (`prices:`) | **Done** (synthetic prices, 180 trading days, fixture day pinned; spec in `price_simulator_spec.md`) |
| Date split (train / validation / test) | `calendar.csv` from the price layer | **Done** (contiguous blocks, test last, each with at least 5 stress days) |
| Household split | `src/advisor_qa/world/split.py`, `split_households.csv` | **Done** (60/20/20 seeded, fixture in train, diagonal with the date split) |
| Ground-truth portfolio impact | `src/advisor_qa/truth/` | **Done** (`Truth.impact`; fixture -$21,700 from the price layer) |
| Questions, mock tools, correct agent, clean traces | `src/advisor_qa/pipeline/` | **Done** (10 question kinds; provisional tools; `clean_run.json` is the generated fixture run) |
| Error injection and hidden labels | `src/advisor_qa/inject/` | **Done** (all 30 modes; placeholder rates and severity) |
| Run and label storage | `src/advisor_qa/store/` | **Done** (per-split JSONL, manifest) |
| Checks, risk score, evaluation at capacity K | `src/advisor_qa/checks/`, `scoring/`, `eval/` | **Done, v1** (30 mapped checks, weighted sum, random vs risk at 5% / 10%) |
| Dashboard | `dashboard/` | Prototype on placeholder data. Update once there are findings. |

## What the household layer gives you
`uv run python -m advisor_qa.world.build --n 750 --out data/synthetic/households` writes four CSVs
(`households`, `accounts`, `holdings`, `instruments`). Every holding has `account_id` and
`instrument_id`. Accounts have `type` (`taxable`, `ira`, `trust`, `401k_held_away`), `in_crm` and
`held_away`. Mutual funds are flagged `is_mutual_fund` and priced end-of-day (`end_of_day_nav`);
each has a `proxy_ticker` for an intraday estimate. Prices are **not** in this layer:
`reference_price` is a nominal number used only to turn dollars into share counts.
The fixture household HH-004217 is always included (known true impact -$21,700, -1.8%).
It needs the SCF file; see `data/README.md`.

## What the price layer gives you
`uv run python -m advisor_qa.world.prices --out data/synthetic/prices [--fred-dir data/raw/fred]`
writes `calendar.csv`, `prices_daily.csv`, `prices_published.csv`, `prices_latent.csv` (and an empty
`events.csv` hook for D4). **All prices are synthetic.** Every CSV starts with a `#` notice line, so
read them with `comment="#"` (`advisor_qa.world.prices.read_price_table`). In Python,
`build_prices(...)` returns a `PriceWorld` with `published_price`, `latent_value` and `prior_close`.
Mutual funds publish the previous day's NAV all day (the D1 setup); `latent_value` and
`prices_latent.csv` are for the truth function and injectors only, never checks or scoring (a test
enforces it). Without `--fred-dir` the bond yields are simulated. On the fixture day at 14:05 the
latent values give HH-004217 a true impact of exactly -$21,700.

## Run everything
```
uv run python -m advisor_qa.world.build --n 750 --out data/synthetic/households
uv run python -m advisor_qa.world.prices --out data/synthetic/prices --fred-dir data/raw/fred
uv run python -m advisor_qa.pipeline.generate --fred-dir data/raw/fred        # about 1 minute
uv run python -m advisor_qa.eval.evaluate --split test                         # report.md
```
Use the same `--fred-dir` (or none) for prices and runs. `generate --rate-scale 0.2` gives about
13% of runs an error instead of about 52% at the literal placeholder rates.

## Interfaces (settled 2026-11-20, see decisions.md)
1. **Price table**: `instrument_id`, date (and time for intraday), price. Mutual funds have one NAV
   per day. `PriceWorld` in Python; CSVs in `data/synthetic/prices/`.
2. **Truth function**: `Truth.impact(household_id, as_of, scope, since=None)` returns dollars and
   percent. `scope` is a list of account_ids; `since` gives week / month / year windows.
3. **Runs and labels**: `runs/{split}.jsonl` (one `RunTrace` per line), `labels/{split}.jsonl`
   (one `RunLabel` per line, keyed by `run_id`), `answers/{split}.jsonl`, `manifest.json`.

## What the runs give you
`advisor_qa.pipeline.generate` writes 10,800 runs (180 days x 60 questions) to
`data/synthetic/runs/`. Each day's questions use households from the same split as the date. The
fixture run (HH-004217, 2026-03-10 14:05) is always clean and equals `tests/fixtures/clean_run.json`.
`advisor_qa.eval.evaluate` writes `report.md`, `report.json` and `flags.csv` per split to
`data/synthetic/eval/`. First results (test split, decisions.md): at the literal placeholder
rates about 52% of runs have an error and risk-based review catches about 1.9x as many as random;
at `--rate-scale 0.2` (13%) it is 7.5x, close to the 7.6x ceiling. Read these as an upper bound
(see "Known gaps" in open_questions.md).

## What is left
Roughly in order. Owners follow the original split: A (world and truth), B (agent and runs),
C (checks and evaluation).

1. **Answers from Lopa (LPL)**: tool inventory and "wrong tool" (B), whether there is a target
   error rate for `rate_scale` (A, B), and whether the firm shows intraday indicative NAVs (A).
   Each is a config or small code change once answered.
2. **Failure rates and severity** (A, B): replace the placeholders; then regenerate and re-run
   the evaluation.
3. **Independent tests for the truth function** (A): hand-calculated cases (leveraged ETF, foreign
   holding, a week-to-date window), and decide what `as_of` on a weekend or holiday should do
   (today it raises an error).
4. **Harder checks to fool** (C): the checks were written next to the mutations, so results are
   optimistic. Options: someone other than the injector's author writes or reviews checks; make
   mutations noisier (e.g. vary the wording of advice and forward-looking claims); add an intent
   classifier for I1 / I5 instead of the rule.
5. **Risk score** (C): better weights than suite severity (single-signal modes such as I4, O3
   and S2 rank below multi-flag runs at K = 5%); a trained scorer stays out of scope for v1.
6. **Rare modes** (A, B): C4, S3, D6, S1, O2, O5 have only a handful of runs per split at the
   placeholder rates. Raise exposure (more leveraged / foreign holders, more sell and tax
   questions) if per-mode results are to be reported.
7. **Dashboard** (`dashboard/`): move from placeholder data to `report.json` and `flags.csv`.

## Definition of done for the dataset
- [x] About 750 households, ~180 market days, ~60 questions a day (`config/default.yaml`).
- [x] Every run has a trace and a label; the same seed gives identical output
  (`tests/integration/test_determinism.py`).
- [x] All 30 failure modes have a mutation and a label (each is tested to change its run).
- [x] The date and household splits are fixed before any checks are tuned (checks were tuned on
  train; the test split was used only for the report).
- [ ] Failure rates and severity confirmed (placeholders now).
