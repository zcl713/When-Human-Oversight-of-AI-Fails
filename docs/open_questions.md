# Open questions

Resolved items move to `decisions.md`.

## Blocks dataset work
- **Number of households, trading days, questions per day**: recommended values are in
  `config/default.yaml` (750 / 180 / 60). Households are drawn without repeating an SCF family, and
  1,160 families qualify, so at most 1,160.
- **Failure-rate placeholders** per Likelihood score (1 = 0.5%, 2 = 1%, 3 = 3%, 4 = 8%, 5 = 15% per
  exposed run). Implemented as placeholders. Because many modes are exposed on most runs,
  they give about 52% of runs an error, which caps the lift of any review at about 2x;
  `injection.rate_scale` (or `generate --rate-scale`) scales them all. 0.2 gives about 13%.
  What prevalence the study should assume is the open part. **Check with Lopa (LPL)** whether
  there is a requirement or an observed error rate to target; the 0.2 used in the first
  evaluation was our own choice, not calibrated to anything. Hints so far (decisions.md,
  2026-11-23): the client workbook suggests seeding "1 in 50" for reviewer experiments; TRAIL's
  research traces have errors in about 97% of traces, but on much harder tasks.
- **Wrong tool (T1)**: built as a team-added mode on our own tool inventory (decisions.md,
  2026-11-23). Worth showing Lopa, since it is not in the client workbook.
- **Severity formula** for the hidden label: a placeholder is implemented (highest suite
  severity + log10(1 + dollar error); `inject/plan.py`). The real formula is still open.

## Blocks checks and evaluation
- **Which checks to build and how risk is computed**: v1 is in place (30 rule-based checks,
  weighted sum). Open: an independent intent classifier instead of the rule for I1/I5, better
  weights than suite severity, and whether checks may read more external data than the CRM
  directory.
- **Capacities K to test**: default 5% and 10%.

## Assumptions to confirm (not from SCF)
These are in `config/default.yaml` or `instruments.csv`. Change them if a collaborator knows better.
- How the platform estimates what it cannot observe: funds from a proxy ETF (target-date funds
  from their equity weight on SPY and BND), the foreign holding from 0.70 x the S&P 500 move.
- 15% of households hold a synthetic foreign-listed equity (`SYN-JPEQ`, not a real security);
  5% of household-days have a same-day deposit or withdrawal (C3). Client names are made up.
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

## Known gaps
Things that work but are weaker than they look. None blocks the current pipeline.
- **Hand-calculated truth cases** (leveraged ETF, foreign holding, a week-to-date window) are still
  missing. The circularity is gone: since 2026-11-23 the correct agent estimates funds instead of
  reading the true value, and tests check that its error equals the estimation error exactly.
- **`as_of` on a weekend or holiday** raises an error instead of snapping to the last close.
- **Checks were written next to the mutations**, so each check knows the trace signature its
  mutation leaves. The 7.5x lift at `rate_scale` 0.2 is close to the ceiling and is an upper bound.
- **Masked modes keep their label.** When two modes on a run interact (e.g. D3 drops the account
  that D4 targeted), the run is still labelled with both, though one had no effect. Some checks
  probably miss for this reason (e.g. D1 recall 0.98 on train; not verified run by run).
- **Rare modes have few runs** at the placeholder rates (train: C4 2, C7 6, O4 7, S1 6, S4 4,
  C3 8, O5 8, D6 9).
- **Workbook setups we only approximate:** C2's prompt ("How are my bonds doing?") is not a
  question kind, so C2 rides on ordinary impact questions; D6 has no separate FX rate (it is
  inside the home close); I6 has turn 3 but no stored turns 1-2.
- **Numbers the agent computed itself** (C2, C3, C4, C6) have no tool output to link to, so their
  `NumberLink.source` is null and the ungrounded-number check (O4) also fires on them; it fires
  on 826 train runs for 7 O4 runs.
- **`events.csv` (D4 hook) is unused.** D4 adds the split to the corporate-actions tool output
  instead.
- **C7 on small moves**: a percent divided by 100 rounds to 0.00% and is indistinguishable from a
  tiny real move, so the check misses some (recall 0.73 on train).

## Nice to have
- Standard errors from SCF replicate weights (`scf2025rw1s`). Not on the team's machines yet. The
  current marginal check uses the sample's own standard error only.
