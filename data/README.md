# data/

| Folder | Contents | Committed? |
|---|---|---|
| `reference/` | Hand-entered tables: instrument archetypes, test catalogue | yes |
| `raw/` | Downloaded source data (SCF, prices, FRED) | no |
| `interim/` | Cleaned intermediate data | no |
| `synthetic/` | Generated tables: household CSVs now; runs and labels files later (format open) | no |

Licence terms for each source are listed below. No real client data anywhere.

## Sources

### Survey of Consumer Finances (SCF) 2025, Summary Extract
- Board of Governors of the Federal Reserve System (2026), Survey of Consumer Finances,
  DOI 10.17016/8799.1. Extract (Excel/CSV): https://www.federalreserve.gov/econres/files/scfp2025excel.zip
  Variable definitions: https://www.federalreserve.gov/econres/files/bulletin.macro.txt
- The source page states no redistribution terms, so the raw file is **not** committed. Put
  `SCFP2025.xlsx` in `data/raw/` or pass `--scf-path` / set `SCF_PATH`.
- 21,800 records for 4,360 families (5 implicates each). `WGT` is per record and sums to the US family
  population. Households are drawn from records with FIN in $250K-$5M and at least $100K of modelled
  holdings (1,160 families, 5,377 records).
- Not used: WRDS/CRSP (terms bar commercial-product use and giving data or metadata to AI).

### What is generated, not SCF
Advisors, account IDs, security-level holdings, ETF vs mutual-fund split, options/leveraged ETF
holders, `in_crm` flags and the fixture household HH-004217. See `config/default.yaml` (`households:`)
and `data/reference/instruments.csv` (`reference_price` is a nominal price, not a quote; each row's `notes`
say what was checked against public factsheets on 2026-11-13 and what was not).
