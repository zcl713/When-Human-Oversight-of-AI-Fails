# Open questions

Resolved items move to `decisions.md`. The owner is who needs the answer to proceed (see `roadmap.md`).

## Blocks dataset work
- **Number of households, trading days, questions per day** (A, B): recommended values are in
  `config/default.yaml` (750 / 180 / 60). Households are drawn without repeating an SCF family, and
  1,160 families qualify, so at most 1,160.
- **Failure-rate placeholders** per Likelihood score (1 = 0.5%, 2 = 1%, 3 = 3%, 4 = 8%, 5 = 15% per
  exposed run) (A, B). Implemented as placeholders. Because many modes are exposed on most runs,
  they give about 52% of runs an error, which caps the lift of any review at about 2x;
  `injection.rate_scale` (or `generate --rate-scale`) scales them all. 0.2 gives about 13%.
  What prevalence the study should assume is the open part.
- **Tool inventory and what counts as a "wrong tool"** (B): confirm with Lopa at LPL. A provisional
  list is in use (see `decisions.md`, 2026-11-20).
- **ID for the "wrong tool" failure mode** (B): the old prototype used T1, pending Lopa's review. Not
  in `FailureModeId` yet.
- **Severity formula** for the hidden label (B): a placeholder is implemented (highest suite
  severity + log10(1 + dollar error); `inject/plan.py`). The real formula is still open.

## Blocks checks and evaluation
- **Which checks to build and how risk is computed** (C): v1 is in place (30 rule-based checks,
  weighted sum). Open: an independent intent classifier instead of the rule for I1/I5, better
  weights than suite severity, and whether checks may read more external data than the CRM
  directory.
- **Capacities K to test** (C): default 5% and 10%.

## Assumptions to confirm (not from SCF)
- The firm shows intraday indicative NAVs for mutual funds and fair values for foreign holdings,
  so a correct answer can match the truth exactly (decisions.md, 2026-11-20). If the real tools
  only show the official NAV, the correct agent would need a proxy estimate and clean runs would
  carry a small tracking error.
- 15% of households hold a synthetic foreign-listed equity (`SYN-JPEQ`, not a real security);
  5% of household-days have a same-day deposit or withdrawal (C3).
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
