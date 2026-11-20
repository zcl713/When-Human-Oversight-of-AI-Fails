"""Hidden labels storage: labels/{split}.jsonl, one RunLabel per line, keyed by run_id.

Evaluation only. Checks and scoring must never import this module (tests/unit/test_separation.py).
"""

from collections.abc import Iterable
from pathlib import Path

from advisor_qa.schema.labels import RunLabel
from advisor_qa.store.runs import LABELS_DIR, canonical_json, read_jsonl, write_jsonl


def write_labels(root: Path, split: str, labels: Iterable[RunLabel]) -> int:
    return write_jsonl(
        root / LABELS_DIR / f"{split}.jsonl",
        (canonical_json(lb.model_dump(mode="json")) for lb in labels),
    )


def read_labels(root: Path, split: str) -> dict[str, RunLabel]:
    out: dict[str, RunLabel] = {}
    for raw in read_jsonl(root / LABELS_DIR / f"{split}.jsonl"):
        lb = RunLabel.model_validate(raw)
        out[lb.run_id] = lb
    return out
