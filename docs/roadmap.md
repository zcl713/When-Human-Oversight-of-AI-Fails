# Roadmap: what is done, what is left, who does what

Last updated 2026-11-13. Decisions behind the finished work are in `decisions.md`; unresolved
questions are in `open_questions.md`.

## Status

| Piece | Where | Status |
|---|---|---|
| Trace schema (39 fields, 7 groups) and hidden `RunLabel` | `src/advisor_qa/schema/` | Done |
| Test catalogue (30 failure modes D/C/I/O/S, scores) | `data/reference/test_catalogue.csv` | Scores done. `mapped_mutation` and `mapped_check` are empty. |
| Households, accounts, holdings, instruments | `src/advisor_qa/world/`, `data/reference/instruments.csv` | **Done** (SCF sampler, fixture HH-004217, checks, build command) |
| Calendar, market days, prices | `src/advisor_qa/world/` | Not started. Real vs synthetic prices is undecided. |
| Date split (train / validation / test) | `config/default.yaml` has fractions only | Not started |
| Ground-truth portfolio impact | `src/advisor_qa/truth/` | Empty |
| Questions, mock tools, correct agent, clean traces | `src/advisor_qa/pipeline/` | Empty |
| Error injection and hidden labels | `src/advisor_qa/inject/` | Empty |
| Checks, risk score, evaluation at capacity K | not scaffolded | Not started |
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

## Agree these three interfaces first
1. **Price table**: `instrument_id`, date (and time for intraday), price. Mutual funds have one NAV per day.
2. **Truth function**: `impact(household, date, scope)` returns dollar and percent impact.
3. **Runs and labels files**: one trace per run (nested, so JSONL is the likely format) and a
   separate labels file keyed by `run_id`. Parquet is not usable on every machine (see README).

## Proposed split (3 people)

### Person A: world and truth (dataset)
- Calendar of market days, prices (real or synthetic, see open questions), the date split with no
  date leakage between train, validation and test.
- Ground-truth impact. Pass the fixture test: -$21,700 on the fixture day moves
  (`tests/unit/test_fixture_household.py`).
- Add a foreign instrument for D6; reuse households across days.
- Inject the **data and calculation errors**: D1-D7 and C1-C7 (14 modes).

### Person B: agent and runs (dataset)
- Question generator across intents, including the ambiguous ones.
- Mock tools, the correct agent, clean traces (`tests/fixtures/clean_run.json` is the target shape).
- Inject the **intent, overconfidence and security errors**: I1-I6, O1-O6, S1-S4 (16 modes).
  Write the hidden `RunLabel` for every injected error.
- Run assembler (runs file plus labels file) and the determinism test
  (`tests/integration/test_determinism.py`, currently skipped).
- If B is overloaded, move S1-S4 to A.

### Person C: checks (QA layer)
- Deterministic checks that read a trace only, mapped to the test catalogue (fill `mapped_check`).
- Risk score: a weighted sum of check flags, as a swappable scorer.
- Evaluation: at capacity K (5% and 10% by default), compare random review with risk-based review
  and report which errors land in the reviewed set.
- Can start now using `tests/fixtures/clean_run.json` and hand-built broken traces. Tune on real
  runs once B delivers them.
- **Hard rule:** checks and scoring never import `advisor_qa.schema.labels`. Only the evaluation
  step compares flags with labels. Add a test that enforces this.

## Definition of done for the dataset
- About 750 households, ~180 market days, ~60 questions a day (`config/default.yaml`).
- Every run has a trace and a label; the same seed gives identical output.
- All 30 failure modes have a mutation (or are explicitly out of scope) and a label.
- The date split is fixed before any checks are tuned.
