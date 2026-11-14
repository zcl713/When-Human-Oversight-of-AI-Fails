# Open questions

Resolved items move to `decisions.md`. The owner is who needs the answer to proceed (see `roadmap.md`).

## Blocks dataset work
- **Prices** (A): real market data or fully synthetic returns? Mutual funds need one NAV per day;
  ETFs need intraday prices; D1 (stale NAV) depends on this split. The household layer is
  price-free by design and does not wait on this.
- **Trace and label storage format** (A, B): Parquet fails on a machine with a Windows application
  control policy (see README). The household layer writes CSV. Traces are nested, so JSONL is likely.
- **Number of households, trading days, questions per day** (A, B): recommended values are in
  `config/default.yaml` (750 / 180 / 60). Households are drawn without repeating an SCF family, and
  1,160 families qualify, so at most 1,160.
- **Failure-rate placeholders** per Likelihood score (1 = 0.5%, 2 = 1%, 3 = 3%, 4 = 8%, 5 = 15% per
  exposed run) (A, B).
- **Tool inventory and what counts as a "wrong tool"** (B): confirm with Lopa at LPL.
- **ID for the "wrong tool" failure mode** (B): the old prototype used T1, pending Lopa's review. Not
  in `FailureModeId` yet.
- **Severity formula** for the hidden label (B): suite severity plus dollar error. `RunLabel.severity`
  is a plain float for now.
- **Foreign instrument for D6** (A): `instruments.csv` has no foreign holding.

## Blocks checks and evaluation
- **Which checks to build and how risk is computed** (C): not scaffolded. Intent ambiguity:
  independent classifier vs rule.
- **Capacities K to test** (C): default 5% and 10%.
- **Format of `NumberLink.source`** (B, C): output hash vs calculation value name. Shape is
  `{number: str as displayed, source: str | null}`, null = ungrounded.

## Assumptions to confirm (not from SCF)
These are in `config/default.yaml` or `instruments.csv`. Change them if a collaborator knows better.
- ETF share of fund dollars (default 0.5; SCF cannot split it). Sweep 0.2 / 0.5 / 0.8.
- 10% of households hold an options or leveraged ETF, at most 3% of their equity.
- 40% of retirement balances are in a target-date fund, chosen by age.
- Cash (`LIQ`) is visible to the advisor and held as a money-market fund. In reality `LIQ` includes
  bank accounts the advisor may not see.
- Generated held-away 401(k)s hold mutual funds only (`held_away_mutual_funds_only`).
- 25 advisors, each household assigned one at random.
- Proxy ETFs for intraday estimates (VUG for the two growth funds, MUB for the muni fund) are our
  judgement. Tickers and durations were checked against public sources on 2026-11-13; see the
  `notes` column of `instruments.csv`.

## Nice to have
- Standard errors from SCF replicate weights (`scf2025rw1s`). Not on the team's machines yet. The
  current marginal check uses the sample's own standard error only.
