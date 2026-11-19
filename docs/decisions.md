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

## Price layer (`src/advisor_qa/world/prices.py`)
Design in `docs/price_simulator_spec.md`; parameters in the `prices:` block of `config/default.yaml`,
each marked calibrated, design or assumption.
- **Real dates, synthetic prices.** The calendar uses real 2026 NYSE trading days because the
  fixture is dated 2026-03-10. Every output is labelled synthetic, so a simulated move is not
  mistaken for a real one. No vendor price data is read: Tiingo, Yahoo and CRSP/WRDS terms do not
  allow storing or sharing it for this work.
- **Yields.** Bond returns use the real FRED DGS2 and DGS10 changes when `--fred-dir` is given
  (blank or `.` values carry the previous value forward; percent changes are divided by 100).
  Bond-equity correlations in the provided dataset are near zero, so independence from simulated
  equities costs little. Simulated yields are the fallback when the files are absent.
- **Simulated-yield correlation sign.** The spec's "yield-equity correlation" of -0.15 calm /
  -0.35 stress is defined as corr(bond return, r_SPY), so corr(yield change, r_SPY) is +0.15 /
  +0.35: stocks down, yields down, bonds up (flight to quality). The config keys are
  `sim_bond_equity_corr_calm` / `_stress` so the sign is unambiguous.
- **Date split.** Contiguous blocks, test last (last 20% test, previous 20% validation). Regimes
  persist, so random dates would leak volatility information between splits. Each split keeps at
  least 5 stress days.
- **Regime resampling: up to 1000 tries, not 20.** With switch probabilities 0.015 / 0.12, only
  about 1% of regime paths meet all of 8-15% stress share and at least 5 stress days in each of
  train, validation (36 days) and test (36 days), so 20 tries fail most seeds, including the
  default. `max_regime_tries` is 1000 (seeds seed, seed+1, ...); if all fail, the build raises an
  error rather than relaxing the constraints. Accepted paths are therefore a conditional sample:
  stress is spread across the splits more evenly than the chain alone would give.
- **Proxies.** VUG for the two growth funds and MUB for the muni fund are kept. VUG fits well and
  MUB is a looser proxy, which is acceptable because tracking noise is what makes the stale-NAV
  case realistic. Proxies that are not in `instruments.csv` are simulated and written as
  `PROXY-VUG` and `PROXY-MUB` (never held). Proxies that are real instruments (SPY, BND, BSV) keep
  their own ids and are not duplicated.
- **Parameter values.** Calibrated values are used where the provided dataset supports them and
  are described as "calibrated to aggregate statistics of a provided dataset". Everything else is
  labelled design or assumption.
- **Leveraged ETFs.** SSO and TQQQ are exact daily-reset 2x and 3x multiples (of the market and
  tech factors), and intraday they are 2x / 3x the return so far.
- **Regime persistence.** Switch probabilities 0.015 (calm to stress) and 0.12 (stress to calm):
  about 11% stress days in runs of about 8, instead of the 17% and 10-day runs measured in one
  crash-dominated window.
- **Fixture day.** 2026-03-10 is forced to stress. At 14:05 the market factor is pinned at -2.1%
  and IVV, VTI, FCNTX, AGTHX and BND are pinned through their own idiosyncratic bridge, so the
  latent moves match the household fixture test exactly; closes stay simulated.
- **File format.** The `time` column is a full timestamp (`2026-03-10T14:05:00`), like the
  fixture. Every CSV starts with a `# SYNTHETIC PRICES ...` line so each file says it is
  synthetic while the columns stay as specified; read with `comment="#"`.
- **Separation.** The latent table lives in `advisor_qa.world.latent`. A test fails if any module
  under a `checks` or `scoring` package (or named so) imports it, names `latent_value`, or reads
  `prices_latent.csv`.
