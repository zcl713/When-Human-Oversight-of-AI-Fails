"""Build the household tables.

`uv run python -m advisor_qa.world.build --n 50 --out data/synthetic/v0 --scf-path <SCFP2025.xlsx>`
"""

import argparse
import os
from pathlib import Path

from advisor_qa.world.checks import (
    check_holdings_sum,
    check_integrity,
    etf_share_realised,
    marginals,
)
from advisor_qa.world.households import generate_world
from advisor_qa.world.instruments import load_instruments
from advisor_qa.world.params import REPO_ROOT, load_config
from advisor_qa.world.scf import eligible_pool, load_scf


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=50, help="households in total, fixture included")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "synthetic" / "v0")
    ap.add_argument(
        "--scf-path",
        type=Path,
        default=Path(os.environ.get("SCF_PATH", REPO_ROOT / "data" / "raw" / "SCFP2025.xlsx")),
    )
    ap.add_argument(
        "--etf-share", type=float, default=None, help="override etf_share_of_fund_dollars"
    )
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--allow-repeat-families", action="store_true")
    args = ap.parse_args()

    seed, params = load_config()
    if args.etf_share is not None:
        params.etf_share_of_fund_dollars = args.etf_share
    if args.allow_repeat_families:
        params.unique_families = False
    seed = args.seed if args.seed is not None else seed

    scf = load_scf(args.scf_path, REPO_ROOT / "data" / "interim" / "scfp2025_subset.csv")
    pool = eligible_pool(scf, params)
    print(f"eligible pool: {len(pool)} records, {pool['YY1'].nunique()} families")
    world = generate_world(pool, load_instruments(), params, args.n, seed)
    world.write(args.out)

    problems = check_integrity(world) + check_holdings_sum(world)
    print("checks:", "PASS" if not problems else problems)
    counts = (len(world.households), len(world.accounts), len(world.holdings))
    print("households {}, accounts {}, holdings {}".format(*counts))
    print(
        f"realised ETF share: {etf_share_realised(world):.3f}",
        f"(param {params.etf_share_of_fund_dollars})",
    )
    print(marginals(world, pool).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
