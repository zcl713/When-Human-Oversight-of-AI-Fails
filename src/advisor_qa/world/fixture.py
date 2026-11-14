"""The hand-built client household HH-004217 (hand-off "fixture household").

Not sampled from the SCF. It exists so a unit test knows the right answer in advance:
true impact = -$21,700 (-1.8% of $1.2M) given the day moves in
`tests/unit/test_fixture_household.py`.
The split of holdings over ACC-88120/88121 was not specified in the hand-off; this is our choice.
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
    ("ACC-88120", "INS-01", 1000),  # $450K large-cap ETF
    ("ACC-88120", "INS-05", 2000),  # $200K growth mutual fund
    ("ACC-88121", "INS-08", 4000),  # $200K intermediate bond ETF (duration case)
    ("ACC-88120", "INS-02", 500),  # $150K large-cap ETF
    ("ACC-88121", "INS-06", 1000),  # $100K growth mutual fund
    ("ACC-88122", "INS-03", 1000),  # $100K total-market ETF, held away
]
