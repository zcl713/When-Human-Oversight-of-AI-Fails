# data/

| Folder | Contents | Committed? |
|---|---|---|
| `reference/` | Hand-entered tables: instrument archetypes, test catalogue | yes |
| `raw/` | Downloaded source data (SCF, prices, FRED) | no |
| `interim/` | Cleaned intermediate data | no |
| `synthetic/` | Generated tables: household CSVs, synthetic price CSVs; runs and labels files later (format open) | no |

Licence terms for each source are listed below. No real client data anywhere.

## Sources

### Survey of Consumer Finances (SCF) 2025, Summary Extract
- Board of Governors of the Federal Reserve System (2026), Survey of Consumer Finances,
  DOI 10.17016/8799.1. Extract (Excel/CSV): https://www.federalreserve.gov/econres/files/scfp2025excel.zip
  Variable definitions: https://www.federalreserve.gov/econres/files/bulletin.macro.txt
- The source page states no redistribution terms, so the raw file is **not** committed. Put
  `SCFP2025.csv` (what the zip contains as of 2026-11) or `SCFP2025.xlsx` in `data/raw/` or pass `--scf-path` / set `SCF_PATH`.
- 21,800 records for 4,360 families (5 implicates each). `WGT` is per record and sums to the US family
  population. Households are drawn from records with FIN in $250K-$5M and at least $100K of modelled
  holdings (1,160 families, 5,377 records).
- Not used: WRDS/CRSP (terms bar commercial-product use and giving data or metadata to AI).

### FRED Treasury yields (optional, for synthetic bond returns)
- Board of Governors of the Federal Reserve System (US), Market Yield on U.S. Treasury Securities
  at 2-Year Constant Maturity, Quoted on an Investment Basis [DGS2], retrieved from FRED, Federal
  Reserve Bank of St. Louis; https://fred.stlouisfed.org/series/DGS2
- Board of Governors of the Federal Reserve System (US), Market Yield on U.S. Treasury Securities
  at 10-Year Constant Maturity, Quoted on an Investment Basis [DGS10], retrieved from FRED, Federal
  Reserve Bank of St. Louis; https://fred.stlouisfed.org/series/DGS10
- Put the downloads in `data/raw/fred/` (`DGS2.csv`, `DGS10.csv`; not committed) and pass
  `--fred-dir data/raw/fred`. Without them the yields are simulated. Tests use made-up files in
  `tests/fixtures/fred/`, never these.

### Prices (synthetic)
**All prices are simulated** by `advisor_qa.world.prices` (`data/synthetic/prices/`). No vendor
price data is read or stored. The dates are real 2026 trading days; the prices are not. Some
parameters are calibrated to aggregate statistics of a provided dataset (volatility, beta,
correlation only; none of its prices, accounts or positions are in the repo); the rest are design
choices or assumptions, marked in `config/default.yaml` (`prices:`). Every price CSV starts with a
`#` notice line, so read it with `pd.read_csv(path, comment="#")` or
`advisor_qa.world.prices.read_price_table`.

### What is generated, not SCF
Advisors, account IDs, security-level holdings, ETF vs mutual-fund split, options/leveraged ETF
holders, `in_crm` flags and the fixture household HH-004217. See `config/default.yaml` (`households:`)
and `data/reference/instruments.csv` (`reference_price` is a nominal price, not a quote; each row's `notes`
say what was checked against public factsheets on 2026-11-13 and what was not).
