"""Runs storage: per-split JSONL of `RunTrace`, answers sidecar, manifest.

Layout under one dataset folder (decisions.md, 2026-11-20):

    runs/{train,val,test}.jsonl      one RunTrace per line, sorted by (timestamp, run_id)
    answers/{train,val,test}.jsonl   {"run_id", "answer"}: the answer text the Answer fields
                                     were derived from (the 39-field trace has no text field)
    labels/{train,val,test}.jsonl    hidden labels (advisor_qa.store.labels; evaluation only)
    split_households.csv             household_id, split
    directory.csv                    CRM directory: household_id, advisor_id, client_name
    manifest.json                    seed, config hash, counts, schema version

Lines are written with sorted keys and no spaces, so the same seed gives byte-identical files.
"""

import hashlib
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from advisor_qa.schema.trace import RunTrace

SCHEMA_VERSION = "1"
SPLITS = ("train", "val", "test")
RUNS_DIR, ANSWERS_DIR, LABELS_DIR = "runs", "answers", "labels"
MANIFEST_FILE, SPLIT_FILE, DIRECTORY_FILE = (
    "manifest.json",
    "split_households.csv",
    "directory.csv",
)


class Manifest(BaseModel):
    schema_version: str = SCHEMA_VERSION
    seed: int
    config_sha256: str
    counts: dict[str, int]  # split -> runs
    error_counts: dict[str, int]  # split -> runs with an injected error
    synthetic_notice: str = "SYNTHETIC: households from the SCF, prices simulated; no client data."


def canonical_json(obj: Any) -> str:
    """Sorted keys, no spaces: the form hashed for output_hash and written to JSONL."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def output_hash(output: Any) -> str:
    """First 12 hex characters of the sha256 of the canonical JSON tool output."""
    return hashlib.sha256(canonical_json(output).encode("utf-8")).hexdigest()[:12]


def _dump(model: BaseModel) -> str:
    return canonical_json(model.model_dump(mode="json"))


def write_jsonl(path: Path, lines: Iterable[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(line + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def write_runs(root: Path, split: str, traces: Iterable[RunTrace]) -> int:
    return write_jsonl(root / RUNS_DIR / f"{split}.jsonl", (_dump(t) for t in traces))


def read_runs(root: Path, split: str) -> Iterator[RunTrace]:
    for raw in read_jsonl(root / RUNS_DIR / f"{split}.jsonl"):
        yield RunTrace.model_validate(raw)


def write_answers(root: Path, split: str, answers: Iterable[tuple[str, str]]) -> int:
    return write_jsonl(
        root / ANSWERS_DIR / f"{split}.jsonl",
        (canonical_json({"answer": a, "run_id": r}) for r, a in answers),
    )


def read_answers(root: Path, split: str) -> dict[str, str]:
    return {
        str(x["run_id"]): str(x["answer"])
        for x in read_jsonl(root / ANSWERS_DIR / f"{split}.jsonl")
    }


def write_manifest(root: Path, manifest: Manifest) -> None:
    (root / MANIFEST_FILE).write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_manifest(root: Path) -> Manifest:
    return Manifest.model_validate_json((root / MANIFEST_FILE).read_text(encoding="utf-8"))
