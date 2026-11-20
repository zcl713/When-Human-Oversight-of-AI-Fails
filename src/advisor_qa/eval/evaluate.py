"""Random review vs risk-based review at capacity K. The only step that reads hidden labels.

`uv run python -m advisor_qa.eval.evaluate --runs data/synthetic/runs --split test
    --out data/synthetic/eval`

At each capacity K (share of runs a reviewer can inspect; config `review_capacity_k`), the
reviewed set is either a random K or the top K by risk score (ties broken by a seeded draw per
run). The reviewer is assumed to catch every error they see (decisions.md, 2026-11-13), so the
comparison is which errors land in the reviewed set. Random review is reported as its expectation
and a 5-95% band over seeded draws.
"""

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from advisor_qa.checks.rules import CHECKS, CrmDirectory, run_checks
from advisor_qa.scoring.weighted import FlagCount, Scorer, WeightedSum
from advisor_qa.store.labels import read_labels
from advisor_qa.store.runs import DIRECTORY_FILE, read_manifest, read_runs
from advisor_qa.world.params import DEFAULT_CONFIG, REPO_ROOT
from advisor_qa.world.streams import stream

RANDOM_DRAWS = 1000


def flag_table(root: Path, split: str) -> pd.DataFrame:
    """One row per run (index run_id), one bool column per check."""
    directory = CrmDirectory.read(root / DIRECTORY_FILE)
    rows = {t.run_id: run_checks(t, directory) for t in read_runs(root, split)}
    return pd.DataFrame.from_dict(rows, orient="index")[list(CHECKS)].astype(bool)


def label_table(root: Path, split: str) -> pd.DataFrame:
    labels = read_labels(root, split)
    return pd.DataFrame(
        {
            "error": [lb.error_present for lb in labels.values()],
            "modes": [[m.value for m in lb.error_type] for lb in labels.values()],
            "severity": [lb.severity for lb in labels.values()],
        },
        index=list(labels),
    )


def risk_order(scores: pd.Series, seed: int) -> list[str]:
    tie = {r: float(stream(seed, "review_tiebreak", r).random()) for r in scores.index}
    return sorted(scores.index, key=lambda r: (-scores[r], tie[r], r))


def _capture(sel: list[str], lab: pd.DataFrame) -> dict[str, float]:
    s = lab.loc[sel]
    errors = int(lab["error"].sum())
    return {
        "reviewed": len(sel),
        "errors_caught": int(s["error"].sum()),
        "recall": float(s["error"].sum() / errors) if errors else 0.0,
        "precision": float(s["error"].mean()) if len(sel) else 0.0,
        "severity_capture": float(s["severity"].sum() / lab["severity"].sum())
        if lab["severity"].sum()
        else 0.0,
    }


def _mode_recall(sel: list[str], lab: pd.DataFrame) -> dict[str, float]:
    out: dict[str, float] = {}
    exploded = lab.explode("modes").dropna(subset=["modes"])
    in_sel = exploded.index.isin(sel)
    for mode, grp in exploded.groupby("modes"):
        out[str(mode)] = float(in_sel[exploded["modes"] == mode].mean()) if len(grp) else 0.0
    return out


def evaluate(
    flags: pd.DataFrame, lab: pd.DataFrame, capacities: list[float], seed: int, scorer: Scorer
) -> dict[str, Any]:
    lab = lab.loc[flags.index]
    scores = flags.apply(lambda row: scorer.score(row.to_dict()), axis=1)
    order = risk_order(scores, seed)
    n = len(flags)
    rng = np.random.default_rng([seed, 99])
    ids = np.array(flags.index)
    result: dict[str, Any] = {
        "runs": n,
        "runs_with_error": int(lab["error"].sum()),
        "prevalence": float(lab["error"].mean()),
        "flagged_any": float(flags.any(axis=1).mean()),
        "capacities": {},
    }
    for k_share in capacities:
        k = math.ceil(k_share * n)
        risk = order[:k]
        draws = [_capture(list(rng.choice(ids, size=k, replace=False)), lab)
                 for _ in range(RANDOM_DRAWS)]  # fmt: skip
        rand = {key: float(np.mean([d[key] for d in draws])) for key in draws[0]}
        band = {key: [float(np.percentile([d[key] for d in draws], q)) for q in (5, 95)]
                for key in ("errors_caught", "recall", "severity_capture")}  # fmt: skip
        rc = _capture(risk, lab)
        result["capacities"][f"{k_share:g}"] = {
            "k": k,
            "risk": rc,
            "random_mean": rand,
            "random_band_5_95": band,
            "lift_recall": rc["recall"] / rand["recall"] if rand["recall"] else None,
            "mode_recall_risk": _mode_recall(risk, lab),
            "mode_recall_random_expected": k / n,
        }
    return result


def check_diagnostics(flags: pd.DataFrame, lab: pd.DataFrame) -> pd.DataFrame:
    """Per mapped check: how often it fires, and its precision / recall for its own mode."""
    lab = lab.loc[flags.index]
    rows = []
    for name, (_, mode) in CHECKS.items():
        fired = flags[name]
        has = lab["modes"].apply(lambda ms, m=mode: m in ms) if mode else lab["error"]
        tp = int((fired & has).sum())
        rows.append(
            {
                "check": name,
                "mode": mode or "(any error)",
                "fires": int(fired.sum()),
                "mode_runs": int(has.sum()),
                "precision": tp / fired.sum() if fired.sum() else float("nan"),
                "recall": tp / has.sum() if has.sum() else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def _markdown(rep: dict[str, Any], diag: pd.DataFrame, split: str, scorer_name: str) -> str:
    lines = [
        f"# Review at capacity K: {split} split (SYNTHETIC data)",
        "",
        f"- runs: {rep['runs']}, with an injected error: {rep['runs_with_error']} "
        f"({rep['prevalence']:.1%}); flagged by at least one check: {rep['flagged_any']:.1%}",
        f"- risk score: {scorer_name}; the reviewer catches every error in the reviewed set",
        "",
        "| K | reviewed | risk: errors caught | random: errors caught (5-95%) | risk recall | "
        "random recall | lift | risk severity capture | random severity capture |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for k, c in rep["capacities"].items():
        r, m, b = c["risk"], c["random_mean"], c["random_band_5_95"]
        lines.append(
            f"| {float(k):.0%} | {c['k']} | {r['errors_caught']} | {m['errors_caught']:.0f} "
            f"({b['errors_caught'][0]:.0f}-{b['errors_caught'][1]:.0f}) | {r['recall']:.1%} | "
            f"{m['recall']:.1%} | {c['lift_recall']:.2f}x | {r['severity_capture']:.1%} | "
            f"{m['severity_capture']:.1%} |"
        )
    first = next(iter(rep["capacities"].values()))
    lines += ["", f"Share of each mode's runs in the risk-reviewed set at K = "
                  f"{float(next(iter(rep['capacities']))):.0%} (random: "
                  f"{first['mode_recall_random_expected']:.1%}):", ""]  # fmt: skip
    lines += [f"- {m}: {v:.0%}" for m, v in sorted(first["mode_recall_risk"].items())]
    lines += [
        "",
        "## Checks (precision / recall for the mode each is mapped to)",
        "",
        "| check | mode | fires | mode runs | precision | recall |",
        "|---|---|---|---|---|---|",
    ]
    for r in diag.itertuples(index=False):
        lines.append(f"| {r.check} | {r.mode} | {r.fires} | {r.mode_runs} | "
                     f"{r.precision:.2f} | {r.recall:.2f} |")  # fmt: skip
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=Path, default=REPO_ROOT / "data/synthetic/runs")
    ap.add_argument("--split", default="test", choices=["train", "val", "test"])
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data/synthetic/eval")
    ap.add_argument("--scorer", default="weighted", choices=["weighted", "count"])
    args = ap.parse_args()

    cfg = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    capacities = [float(x) for x in cfg["review_capacity_k"]]
    seed = read_manifest(args.runs).seed
    scorer: Scorer = WeightedSum.from_catalogue() if args.scorer == "weighted" else FlagCount()
    flags = flag_table(args.runs, args.split)
    lab = label_table(args.runs, args.split)
    rep = evaluate(flags, lab, capacities, seed, scorer)
    diag = check_diagnostics(flags, lab)
    out = args.out / args.split
    out.mkdir(parents=True, exist_ok=True)
    flags.to_csv(out / "flags.csv", index_label="run_id")
    (out / "report.json").write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    md = _markdown(rep, diag, args.split, args.scorer)
    (out / "report.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
