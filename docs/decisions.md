# Decisions log

## 2026-11-13: scope for version 1
- **Reviewer data is dropped.** No reviewer model, no Review field group, no H1-H8 tests, no pilot.
  Inspection is a pure selection comparison: at capacity K, which errors land in the reviewed set,
  assuming the reviewer catches what they see.
- **No model is trained in version 1.** Checks are deterministic code. The risk score is a swappable
  scorer; v1 is a weighted sum of flags. Logistic regression and gradient boosting are on hold.
- **Hidden labels are evaluation-only.** Stored separately from traces; never imported by checks or scoring.
- **A held-out test split is defined from the start** (by household and by date).

## 2026-11-13: repo layout
- Repo root is the pipeline project (`src/advisor_qa`). The earlier prototype dashboard lives in
  `dashboard/` on placeholder data and is untouched until we have findings.
- Only the data synthesis pipeline is scaffolded. Checks, scoring and evaluation are added when
  we design them; how risk is computed and which checks to build are not settled.
- Tooling: uv, ruff, pytest, mypy, poethepoet (no Makefile, so it works on Windows and macOS).

## 2026-11-13: household layer (`src/advisor_qa/world`)
- **Source:** 2025 SCF Summary Extract (needed because total financial assets, FIN, is only there).
  Wealth band: FIN $250K-$5M. Modelled holdings: stocks, funds, bonds, CDs, liquid cash, IRAs,
  employer 401(k)s, trusts. Records with under $100K modelled are dropped. Pool: 5,377 records from
  1,160 families. WRDS/CRSP is not used (terms bar commercial-product use and giving data to AI).
- **Sampling:** pool the records (5 per family) and draw by `WGT`, never the same family twice. No
  noise is added to households. At n=750 every marginal (age, total value, share with IRA / 401(k) /
  trust) is within about 2 standard errors of the SCF. Drawing 65% of all families without
  replacement distorts the sample slightly; with repeats allowed at n=10,000 it matches closely.
- **`total_value`** is the sum of the generated holdings (cash included), not FIN. Quantities are
  fractional to 4 decimals; `value_usd` is derived. SCF dollars and `total_value` differ by cents.
- **Instruments:** 21 archetypes. SCF cannot split fund dollars into ETF vs mutual fund, so
  `etf_share_of_fund_dollars` is a parameter. Options and leveraged ETFs are our assumption.
  Mutual funds are `end_of_day_nav` and carry an intraday `proxy_ticker`.
- **Accounts:** `taxable`, `ira`, `trust`, `401k_held_away`. Every account is `in_crm` in the clean
  data; D2 removes the held-away account later. Generated 401(k)s hold mutual funds only.
- **Fixture HH-004217:** hand-built, bypasses the sampler, accounts ACC-88120/88121/88122. The split
  across the first two accounts is our choice. Its held-away holding is an ETF (as in the hand-off),
  and `tests/fixtures/clean_run.json` now matches it.
- **Trace schema:** `Holding` gained `account_id` and `instrument_id` (39 fields). D2 needs them.
- **Output format:** CSV, because Parquet cannot load on a machine with an application control policy.
