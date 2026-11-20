# Made-up yield files for tests

`DGS2.csv` and `DGS10.csv` here are **made up** (seeded random walks in percent), not FRED data.
They only mimic the two FRED download layouts: `DGS10.csv` uses the newer `observation_date`
header with blank missing values, `DGS2.csv` the older `DATE` header with `.` for missing values.
Real FRED downloads go in `data/raw/fred/` (gitignored) and are never committed.
