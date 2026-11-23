"""The hand-built client household HH-004217 (the client test suite's $1.2M household).

Not sampled from the SCF. It exists so a unit test knows the right answer in advance:
true impact = -$21,700 (-1.8% of $1.2M) given the day moves in
`tests/unit/test_fixture_household.py`. Holdings, accounts and moves follow the "Scenario Model"
sheet of the client workbook (Sample_Test_Suite.xlsx). Its "balanced mutual fund" is mapped to our
2030 target-date fund (60% equity) and its held-away "target-date fund" to the 2040 fund.
Quantities use the nominal reference prices in data/reference/instruments.csv.
"""

FIXTURE_HOUSEHOLD_ID = "HH-004217"
FIXTURE_ADVISOR_ID = "ADV-0382"
FIXTURE_ACCOUNT_IDS = ("ACC-88120", "ACC-88121", "ACC-88122")
FIXTURE_AGE = 58

# (account_id, account type, instrument_id, qty)
FIXTURE_ACCOUNTS: list[tuple[str, str]] = [
    ("ACC-88120", "taxable"),
    ("ACC-88121", "ira"),
    ("ACC-88122", "401k_held_away"),
]
FIXTURE_HOLDINGS: list[tuple[str, str, float]] = [
    ("ACC-88120", "INS-01", 1000),  # $450K large-cap equity ETF, -2.1%
    ("ACC-88120", "INS-05", 2000),  # $200K growth equity mutual fund, -2.0% (D1)
    ("ACC-88120", "INS-08", 4000),  # $200K intermediate bond ETF, -1.2% (C2)
    ("ACC-88121", "INS-02", 500),  # $150K equity index ETF, -2.1%
    ("ACC-88121", "INS-14", 4000),  # $100K balanced mutual fund (2030 target date), -1.2% (D1)
    ("ACC-88122", "INS-15", 2500),  # $100K target-date fund (2040), held away, -1.5% (D2)
]
