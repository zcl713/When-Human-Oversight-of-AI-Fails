# AI Oversight QA Dashboard

Interactive dashboard for the capstone on human oversight of AI in wealth management. It compares
random sampling with risk-based review for catching high-impact errors from an AI advisor assistant.
**All numbers other than the severity, likelihood and detectability scores are simulated and
illustrative.** They will be replaced once the experiment has run.

Two drafts share the same data, code and styles:

| Draft | URL | What it is |
|---|---|---|
| A, lean | `lean/` | One page: verdict, four headline tiles, budget curve, misses by error type, snowball, quality over time |
| B, full | `/` (root) | Seven tabs. The first, Summary, shows the same blocks as Draft A; the rest go deeper |

Old links keep working: `#register` opens the Failure-mode register tab, `#strategies` the Strategy comparison.

## Run it

- **On GitHub Pages:** Settings → Pages → Deploy from a branch → `main` / root. The full version is at
  `https://<your-username>.github.io/<repo>/`, the lean one at `.../lean/`.
- **Locally:** the pages load their data with `fetch`, so serve the folder rather than double-clicking
  the file: `python3 -m http.server`, then open http://localhost:8000 (or /lean/).

## What is where

| Path | What it is |
|---|---|
| `index.html` | Draft B: tabs and all fixed text |
| `lean/index.html` | Draft A: the one-page summary |
| `styles.css` | Theme colors (light and dark) and layout, shared by both drafts |
| `js/shared.js` | Shared by both drafts: formatting, Wilson intervals, sliders, verdict sentence, the Summary blocks |
| `js/app.js` | Draft B only: register, strategy detail, snowball graph, reviewer reliability, method tab, tabs |
| `js/dash-shim.js` | Loads `data/*.json` (listed in its script tag) and redraws on data, tab, slider or width change |
| `data/*.json` | The data behind every number and chart, written by the generator |
| `lib/d3.min.js` | d3 v7.9.0, included so the site works offline |
| `tools/generate_sample_data.py` | Rebuilds `data/` from the test-suite workbook (fixed random seed) |

## Definitions

- **High-impact error:** an injected error whose failure type has RPN ≥ `HIGH_IMPACT_RPN` (60, the
  Critical tier). Change it in one place, the generator, then regenerate.
- **Error type:** Reasoning, Planning or System execution, the step of the agent's workflow where
  the failure starts. Human review is not an error type; it is the control layer.
- **Errors missed:** never selected for review + reviewed but not caught.
- **Lopa's priority failure modes** (`LOPA_MODES`): calculation (C1, C2, C6), intent (I1, I2) and
  wrong tool (T1). In the strategy simulation only, each is oversampled to at least
  `MIN_INJECTED_PER_MODE` (15) injected errors per run by reassigning errors from the most common
  other failure types, so the total stays the same. The quality series uses the natural mix.
- **Team-added failure modes** (`TEAM_ADDED`): test cases the team added without editing the
  workbook. T1 "Wrong tool selected" (Planning, S4 L3 D4, RPN 48) is pending Lopa's review. It is
  injected but not high-impact while its RPN is below `HIGH_IMPACT_RPN`.
- **Percentages:** every displayed percentage is a displayed count ÷ its displayed denominator
  (`share()` in `js/shared.js`). Curves may plot unrounded means; text quoting them uses counts.

## The data

`tools/generate_sample_data.py` runs a seeded Monte Carlo (500 runs) with three stages: errors are
injected into agent answers, answers are selected for review (at random or by guardrail risk score),
and reviewers review them. Both strategies use the same reviewer model: a base catch rate lowered by
fatigue, load above team capacity, and plausible-looking answers. Every assumption is a named
constant marked `ASSUMPTION` at the top of the file; the Method tab lists them all.

| File | What it holds |
|---|---|
| `risks.json` | The test suite: scores, RPN, tier, error type or control layer, illustrative sample results |
| `sim_grid.json` | Results for every review budget (2–30%) × reviewer catch rate (40–90%) × strategy, overall, by error type and by Lopa's mode; drives the sliders |
| `sim_meta.json` | Definitions, defaults, team capacity, Lopa's modes, share caught at team capacity (`at_capacity`), the best achievable share (`ceiling`) per catch rate, and the assumptions list |
| `snowball.json` | Stale-price case: outcome per strategy, time series, dependency graph |
| `quality.json` | 14 days of the random review slice (one fixed-seed run): raw rate, canaries caught / seen to date, reviewer sensitivity, the daily and 3-day pooled corrected estimates with 95% intervals, true rate, alert days under the current and old rule, and the 500-run evaluation of both rules |
| `oversight.json` | Reviewer catch rate by condition (illustrative) |

Counts are whole-number means over the runs, with the middle 95% of runs as the range; by-type
counts are apportioned so they add up to the totals. Proportions measured from a sample use Wilson
intervals (`wilson()` in `js/shared.js`), never the normal approximation.

**Quality correction.** Each day `CANARY_PER_DAY` (4) of the 40 random reviews are seeded known errors.
Reviewer sensitivity = canaries caught ÷ canaries seen to date. Corrected error rate = raw rate in the
other 36 reviews ÷ sensitivity, capped at 100%; its 95% interval comes from `CORRECTION_DRAWS` (2,000)
draws of the two Beta posteriors.

**Quality alert rule.** No alert until `MIN_CANARIES_FOR_ALERT` (20) canaries have been seen. From then
on, errors found and reviews are pooled over a trailing `ALERT_WINDOW_DAYS` (3) window, corrected with
the cumulative sensitivity, and an alert fires on every day this pooled estimate is above `ALERT_LIMIT`
(8%). The chart shows one fixed-seed run; `quality.json` also holds an `evaluation` of this rule and the
old one (`ALERT_DAYS` days in a row on the daily estimate) over `QUALITY_EVAL_RUNS` (500) simulated
runs: runs with a false alarm before drift, median days from drift start to the first alert after it,
and runs with no such alert. These runs are drawn after every other output, so they change nothing else.

## Editing

- **Change a score:** edit the workbook and regenerate, or edit `data/risks.json` (keep `rpn`,
  `tier` and `high_impact` in step). Note that the simulation uses the workbook, so regenerate for it to
  pick up score changes.
- **Change an assumption or the high-impact threshold:** edit the constants at the top of the
  generator, then regenerate.
- **Add or rescore a team-added failure mode:** edit `TEAM_ADDED` in the generator (and
  `LOPA_MODES` if it is a priority mode), then regenerate. Do not edit the workbook for these.
- **Change RPN tier thresholds:** `THRESHOLDS` in `js/shared.js` (the generator's `tier()` uses the same values).
- **Change wording:** `index.html`, `lean/index.html`, and the summary text in `js/shared.js`.
- **Regenerate the sample data** (needs `openpyxl`):
  `python3 tools/generate_sample_data.py path/to/Sample_Test_Suite.xlsx data/`
