# Roadmap: what is done, what is left, who does what

Last updated 2026-11-20. Decisions behind the finished work are in `decisions.md`; unresolved
questions are in `open_questions.md`.

## Status

| Piece | Where | Status |
|---|---|---|
| Trace schema (39 fields, 7 groups) and hidden `RunLabel` | `src/advisor_qa/schema/` | Done |
| Test catalogue (30 workbook modes D/C/I/O/S + team-added T1) | `data/reference/test_catalogue.csv` | **Done** (scores, `mapped_mutation`, `mapped_check`, `origin`) |
| Households, accounts, holdings, instruments | `src/advisor_qa/world/`, `data/reference/instruments.csv` | **Done** (SCF sampler, fixture HH-004217, checks, build command) |
| Calendar, market days, prices | `src/advisor_qa/world/prices.py`, `config/default.yaml` (`prices:`) | **Done** (synthetic prices, 180 trading days, fixture day pinned; spec in `price_simulator_spec.md`) |
| Date split (train / validation / test) | `calendar.csv` from the price layer | **Done** (contiguous blocks, test last, each with at least 5 stress days) |
| Household split | `src/advisor_qa/world/split.py`, `split_households.csv` | **Done** (60/20/20 seeded, fixture in train, diagonal with the date split) |
| Ground-truth portfolio impact | `src/advisor_qa/truth/` | **Done** (`Truth.impact`; fixture -$21,700 from the price layer) |
| Questions, mock tools, correct agent, clean traces | `src/advisor_qa/pipeline/` | **Done** (12 question kinds; our 15-tool inventory; funds estimated from proxies; `clean_run.json` is the generated fixture run) |
| Error injection and hidden labels | `src/advisor_qa/inject/` | **Done** (30 workbook modes + team-added T1; setups follow the client workbook; placeholder rates and severity) |
| Run and label storage | `src/advisor_qa/store/` | **Done** (per-split JSONL of OTLP traces, labels, manifest) |
| OpenTelemetry traces and the QA view | `src/advisor_qa/otel/`, `src/advisor_qa/qa/` | **Done** (GenAI semconv v0.1.0, content capture on; the 39-field RunTrace is derived from spans) |
| Checks, risk score, evaluation at capacity K | `src/advisor_qa/checks/`, `scoring/`, `eval/` | **Done, v1** (31 mapped checks, weighted sum, random vs risk at 5% / 10%) |
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
`data/synthetic/eval/`. Results on the test split (2026-11-23; read them as an upper bound, see
"Known gaps" in open_questions.md):

| `rate_scale` | runs with an error | caught at K = 5%: risk vs random | at K = 10% |
|---|---|---|---|
| 1.0 (placeholders as written) | 53% | 108 vs 58 | 216 vs 115 |
| 0.2 | 13% | 108 vs 14 | 216 vs 28 |
| 0.04 (near the workbook's "1 in 50") | 3% | 65 of 65 vs 3 | 65 of 65 vs 6 |

## What is left
Roughly in order. There are no fixed roles: anyone picks up any item.

1. **Answers from Lopa (LPL)**: whether there is a target error rate for `rate_scale`, and a
   look at the team-added T1 and our tool inventory.
2. **Failure rates and severity**: replace the placeholders; then regenerate and re-run the
   evaluation.
3. **Hand-calculated tests for the truth function** (leveraged ETF, foreign holding, a
   week-to-date window), and decide what `as_of` on a weekend or holiday should do (today it
   raises an error).
4. **Make the evaluation honest** (the biggest gap now): clean runs have no noise, so anything
   flagged is an error and top-K review stays near perfect. In order: (a) benign noise in clean
   runs (retries, disclosed stale prices, paraphrased refusals, harmless unlinked figures,
   skipped restatements on clear questions); (b) held-out mutation variants (tune checks on one
   way a mode happens, evaluate on another); (c) errors of varying size; (d) someone other than
   the injector's author reviews the checks.
5. **Risk score**: better weights than suite severity; a trained scorer stays out of scope for v1.
6. **Rare modes**: C4, C7, O4, S1, S4 have only a handful of runs per split. Raise exposure
   (more leveraged holders, more rates / inception questions) if per-mode results are reported.
7. **Dashboard** (`dashboard/`): move from placeholder data to `report.json` and `flags.csv`.

## Definition of done for the dataset
- [x] About 750 households, ~180 market days, ~60 questions a day (`config/default.yaml`).
- [x] Every run has a trace and a label; the same seed gives identical output
  (`tests/integration/test_determinism.py`).
- [x] All 31 failure modes (30 from the workbook + T1) have a mutation and a label (each is
  tested to change its run and to be caught by its check).
- [x] The date and household splits are fixed before any checks are tuned (checks were tuned on
  train; the test split was used only for the report).
- [ ] Failure rates and severity confirmed (placeholders now).
