# Price simulator spec (synthetic price layer)

## 1. Purpose and scope

The price layer simulates prices for the 21 instruments in `data/reference/instruments.csv` (plus two proxy series) over about 180 trading days, because none of the real-data sources we checked lets us store, share or hand over the data: Tiingo's terms bar saving it and building an AI evaluation dataset, Yahoo's download is paywalled, and CRSP/WRDS terms restrict sharing and commercial use. It fills the "Calendar, market days, prices" row of `docs/roadmap.md` and gives interface 1 of the roadmap (the price table) and the inputs for interface 2 (the truth function) and the error injectors.

It has to support five things: D1 stale mutual fund NAV, timestamp errors (C1, D5), the bond duration case (C2), leveraged instruments (C4), and a true dollar impact at any query time. It is not a market model. Every price is synthetic and must be labelled that way in the data README and in anything shown to LPL. No vendor data is read. The only optional outside input is FRED Treasury yields (Federal Reserve data), used to drive bond returns.

## 2. Outputs and interface

The module is `src/advisor_qa/world/prices.py`, run as `python -m advisor_qa.world.prices --out data/synthetic/prices`, with settings under a new `prices:` block in `config/default.yaml`. It writes four CSVs, in the same style as the household layer. Timestamps are New York time, written like the fixture (`2026-03-10T14:05:00`).

| File | Columns | Who may read it |
| --- | --- | --- |
| `calendar.csv` | `market_date`, `regime` (calm or stress), `split` (train, val, test), `is_fixture_day` | everyone |
| `prices_daily.csv` | `instrument_id`, `market_date`, `prior_close`, `close`, `price_type` (close or nav), `close_as_of` | everyone |
| `prices_published.csv` | `instrument_id`, `market_date`, `time`, `price`, `price_as_of` on a 5-minute grid from 09:30 to 16:00 | tools and traces |
| `prices_latent.csv` | `instrument_id`, `market_date`, `time`, `value` | the truth function and error injectors only |

Published and latent differ only for mutual funds. An ETF's published price at time t equals its latent value. A fund's published price during the day is the previous day's NAV, stamped 16:00 the day before, which is what makes D1 possible. The fund's latent value moves during the day like its proxy, and its closing NAV equals the latent value at 16:00.

Python interface, all keyed by `instrument_id` and a timestamp:

- `build_prices(instruments, cfg, seed, fred_dir=None) -> PriceWorld`
- `PriceWorld.published_price(instrument_id, ts) -> (price, price_as_of)`
- `PriceWorld.latent_value(instrument_id, ts) -> float` (truth only)
- `PriceWorld.prior_close(instrument_id, date) -> float`

Query times snap down to the 5-minute grid. Checks and scoring must never import the latent file or `latent_value`, the same rule the roadmap sets for `advisor_qa.schema.labels`.

## 3. Model

Each day's close-to-close return comes from one market factor and a small idiosyncratic term per instrument. The intraday path is then a Brownian bridge from the prior close to that close.

**Calendar.** 180 real NYSE trading days starting 2026-01-02 (ending 2026-09-21), using the 2026 exchange holidays (Jan 1, Jan 19, Feb 16, Apr 3, May 25, Jun 19, Jul 3, Sep 7). The fixture day 2026-03-10 is trading day 46. The dates are real but the prices are not, so label every output as synthetic. Simulate one extra day before the window to give the first day a prior close.

**Regimes.** A two-state Markov chain, calm and stress, starting calm. Defaults give about 11% stress days in runs of about 8. Resample (seed, seed+1, … up to 20 tries) until each of train, val and test has at least 5 stress days.

**Split.** Contiguous blocks by date: the last 20% of days are test, the 20% before them are validation, and the rest are train, matching `test_date_fraction`. Regimes persist from day to day, so random dates would leak volatility information between splits. The fixture day falls in train.

**Market factor.** The daily market return is Student-t (5 degrees of freedom), scaled to a calm or stress daily volatility, with a small positive drift in calm and a negative one in stress. `r_SPY` is the market factor itself. A tech factor, `r_tech = 1.2 × r_SPY + noise`, is the underlying for TQQQ.

**Daily return per instrument** is `β × r_SPY + idio`, with the rules below. Proxy series for VUG and MUB are simulated too, but never held.

| Instrument group | Rule |
| --- | --- |
| SPY, IVV, VFIAX | `r_SPY` (IVV and VFIAX add tiny idio; VFIAX uses SPY as proxy) |
| VTI, SCHD, JEPI | β of 1.00, 0.92 and 0.60 on `r_SPY`, each with its own idio |
| SSO, TQQQ | 2 × `r_SPY` and 3 × `r_tech`, reset daily, so intraday is also 2× or 3× the return so far |
| FCNTX, AGTHX | proxy VUG (β 1.02 on `r_SPY`) plus fund idio |
| BND, GOVT, BSV, and funds VBTLX, VBIRX, VWIUX | `−duration × Δyield + yield/252 + idio`; 10-year yield for duration above 3, 2-year for the rest; VBTLX proxy BND, VBIRX proxy BSV, VWIUX proxy MUB with β 0.8 on the 10-year |
| VTHRX, VFORX, VFIFX, VTINX | `w × r_SPY + (1 − w) × r_BND + idio`, no proxy ETF; w by vintage in the parameters table |
| VMFXX | NAV fixed at 1.00 |

**Yields.** If `fred_dir` is given, read `DGS2.csv` and `DGS10.csv` (the FRED downloads) and carry the previous value over days where the value is blank (older FRED downloads use a dot). Bond returns then use real yield changes. The cost is that bond returns are independent of the simulated equity returns, so there is no flight-to-quality effect. If `fred_dir` is absent, simulate the yield changes instead, with a small negative correlation to `r_SPY` that is stronger in stress.

**Intraday path.** Value at grid time t is `P0 × (1 + x(t))`, where `x` is a path from 0 to the day's return `R` over the grid's 79 points, `x(t) = R·s + σ_intra × (W(s) − s·W(1))` with `s` the fraction of the day elapsed. Build one bridge for the market factor, one for the tech factor, and one idio bridge per instrument, then combine them with the same βs so that related instruments move together. The path starts at the prior close and ends exactly at the close.

**Anchoring.** Scale each instrument's whole series by one constant so that its close on 2026-03-09 equals `reference_price`. Returns are unchanged. This keeps the fixture household at exactly $1.2M, and every sampled household equals its SCF value on that date and drifts after it.

## 4. Fixture day

On 2026-03-10 at 14:05 the six fixture instruments must show exactly the moves in `DAY_MOVES` in `tests/unit/test_fixture_household.py`, so HH-004217's true impact is −$21,700 (−1.8% of $1.2M). Those moves are scripted, not drawn.

| Instrument | Held as | Latent move at 14:05 |
| --- | --- | --- |
| INS-01 SPY | ETF | −2.1% |
| INS-02 IVV | ETF | −2.1% |
| INS-03 VTI | ETF, held away | −2.1% |
| INS-05 FCNTX | mutual fund | −1.5% |
| INS-06 AGTHX | mutual fund | −2.0% |
| INS-08 BND | bond ETF | −1.0% |

How to do it:

1. Force 2026-03-10 into the stress regime.
2. Pin the market factor's return so far at 14:05 to −2.1%, since SPY is the market factor.
3. For each of the other pinned instruments, pin its path at 14:05 to its value in the table. Build the path as two bridges, 09:30 to 14:05 and 14:05 to 16:00, so it passes through the pinned point.
4. Let everything after 14:05 run freely, so the day's close is simulated, not scripted. Instruments not in the table follow the market factor normally.
5. The funds' published NAV on that day stays the 2026-03-09 close, so the true move and the published price differ, which is the D1 setup.

The pinned values override the factor model only at that one timestamp. If the bond pin conflicts with the FRED yield change on that date, the pin wins and the day is noted in `calendar.csv` as `is_fixture_day`.

## 5. Parameters

Settings marked **calibrated** come from aggregate statistics (volatility, beta, correlation) of an ETF position-and-price dataset covering 2015-05 to 2020-09 that was provided for this work. Only those aggregate figures are used. None of its prices, accounts or positions appear in the repo or in any output. Settings marked **design** are choices made for the experiment. Settings marked **assumption** have no data behind them; either replace them with a figure from a public source we can cite, or keep them and say they are assumptions.

The provided dataset's prices carry timing noise. Two ETFs on near-identical indexes correlate at only 0.90 where about 0.99 is expected, and a muni fund tracks its proxy ETF at 0.73. So tracking-error settings are chosen by design, and the dataset is not a price source. A WRDS/CRSP pull of aggregate statistics only could later refine the fund tracking and JEPI settings, but nothing in this spec depends on it.

| Parameter | Default | Basis |
| --- | --- | --- |
| `n_days`, `start_date` | 180, 2026-01-02 | matches `config/default.yaml` |
| `fixture_date`, `fixture_query_time` | 2026-03-10, 14:05 | from the fixture and `clean_run.json` |
| Date split | last 20% of days test, previous 20% validation, rest train | design; contiguous blocks |
| Regime switch, calm to stress | 0.015 per day | design; gives about 11% stress days (measured 17%, in a window dominated by one crash) |
| Regime switch, stress to calm | 0.12 per day | design; runs of about 8 days (measured median 10) |
| Market daily volatility, calm / stress | 0.75% / 2.4% | calibrated; measured 0.77% / 2.33% |
| Market daily drift, calm / stress | +0.04% / −0.15% | design; stress days skew down (measured mean about +0.05% in both regimes) |
| Market return distribution | Student-t, 5 degrees of freedom | assumption; fat tails on stress days |
| Intraday volatility, share of daily | 0.6 (equities), 0.3 (bonds) | assumption |
| Tech factor | 1.2 × market + 0.5% noise | assumption; underlying for TQQQ |
| β on market | VTI 1.00, SCHD 0.92, VUG 1.02, JEPI 0.60 | calibrated for VTI, SCHD, VUG (measured 1.00, 0.92, 1.02); assumption for JEPI |
| Idio volatility per day | VTI 0.10%, SCHD 0.37%, VUG 0.38% | calibrated (measured 0.12%, 0.37%, 0.38%) |
| Idio volatility per day | IVV 0.01%, VFIAX 0.02%, JEPI 0.30% | design for IVV and VFIAX (the measured 0.1% is mostly timing noise); assumption for JEPI |
| Growth funds vs proxy VUG, idio per day | 0.25% | assumption |
| Muni fund vs proxy MUB, idio per day | 0.17% | calibrated; includes timing noise |
| Bond idio volatility per day | 0.08% | assumption |
| Bond–equity link (FRED mode) | none | calibrated; measured correlations BND 0.08, BSV −0.10, GOVT −0.47 |
| Target-date equity weight | income 0.30, 2030 0.60, 2040 0.75, 2050 0.90 | assumption |
| Simulated yield change (no FRED), calm / stress | 4 bp / 8 bp per day | assumption |
| Yield–equity correlation (no FRED), calm / stress | −0.15 / −0.35 | assumption |
| Minimum stress days per split | 5 | design; resample the seed until met |

## 6. Acceptance tests

Add these under `tests/unit/test_prices.py`. The module is done when all pass and the existing fixture test passes against it.

1. **Determinism.** The same seed gives byte-identical CSVs.
2. **Fixture.** Using the pinned 14:05 values, `Σ qty × (latent_value − prior_close)` over HH-004217's six holdings is −21,700 within one cent, and the ratio to $1,200,000 is −0.018 ± 0.0001. The 2026-03-09 close of each fixture instrument equals its `reference_price`.
3. **Path integrity.** For every instrument and day, the first grid value equals the prior close, the last equals the close, nothing is NaN, and every price is above zero.
4. **Fund publishing.** A fund's published price on any grid time is the previous NAV with `price_as_of` of the previous day 16:00. VMFXX is 1.00 on every day.
5. **Proxy tie.** Daily-return correlation with the proxy is at least 0.98 for the index funds and between 0.85 and 0.97 for FCNTX and AGTHX.
6. **Leverage.** Each day, SSO's return is 2 × SPY's and TQQQ's is 3 × the tech factor's.
7. **Bonds.** With FRED files loaded, BND's daily return has correlation of at least 0.9 with −Δ(10-year yield).
8. **Regimes.** Stress days are 8% to 15% of the calendar, each split has at least 5, and the ratio of stress to calm daily volatility is between 2.5 and 4.
9. **Sanity.** No unleveraged instrument moves more than 15% in a day, no leveraged one more than 40%.
10. **Separation.** `published_price` never returns a latent value for a mutual fund, and a test fails if `advisor_qa.checks` or the scoring code imports the latent file or `latent_value`.

## 7. Hooks and out of scope

Version 1 leaves these to the error injectors and later work, but the layer leaves room for them:

- **D4 corporate action.** An empty `events.csv` (`instrument_id`, `market_date`, `type`, `ratio`) that the injector fills. The price path stays unadjusted; the injector decides what the agent sees.
- **D7 conflicting price sources.** The injector can add a second published price with noise. Nothing is stored for it in v1.
- **D6 foreign holding.** Needs an instrument with its own trading hours and calendar. `instruments.csv` has none yet, so a foreign instrument has to be added first.
- **Fund distributions.** Ignored. NAVs have no ex-dividend drops.
- **C3 same-day cash flows.** Not a price issue; it belongs to the household and truth layers.
- **Real data later.** If a licensed source clears, it only has to produce the same four files. Nothing else in the pipeline changes.

## 8. Design decisions

- **Real dates, synthetic prices.** The calendar uses real 2026 dates because the fixture is dated 2026-03-10. Every output is labelled synthetic, so a simulated move is not mistaken for a real one.
- **Yields.** Bond returns use the real DGS2 and DGS10 changes for 2026. Bond–equity correlations in the provided dataset are near zero, so independence from simulated equities costs little. Simulated yields are the fallback when the files are absent.
- **Date split.** Contiguous blocks, with test last. Regimes persist, so random dates would leak volatility information between splits. Each split keeps at least 5 stress days.
- **Proxies.** VUG for the growth funds and MUB for the muni fund are kept. VUG fits well (correlation 0.97 with the market), and MUB is a looser proxy for the muni fund, which is acceptable because tracking noise is what makes the stale-NAV case realistic.
- **Parameter values.** Calibrated values are used where the provided dataset supports them and are described as "calibrated to aggregate statistics of a provided dataset". Everything else stays labelled as design or assumption.
- **Leveraged ETFs.** SSO and TQQQ are exact daily-reset 2× and 3× multiples, since neither appears in the provided dataset.
- **Regime persistence.** Switch probabilities of 0.015 (calm to stress) and 0.12 (stress to calm), giving about 11% stress days in runs of about 8, instead of the 17% and 10-day runs measured in one crash-dominated window.

## 9. Implementation order

1. Calendar, holidays, regime chain and the split, with the stress-day resampling. Test 8.
2. Market and tech factors, daily returns for ETFs and leveraged ETFs. Tests 6 and 9.
3. Bond returns from FRED files, with the simulated-yield fallback. Test 7.
4. Mutual funds, proxies and target-date blends, with published-versus-latent. Tests 4 and 5.
5. Intraday bridges on the 5-minute grid and the anchoring step. Test 3.
6. Fixture pinning on 2026-03-10 at 14:05. Test 2.
7. CSV writer, CLI, `prices:` config block and the determinism and separation tests. Tests 1 and 10.

The truth function (interface 2) can then be written against `latent_value` and `prior_close`, and the error injectors against `published_price`.
