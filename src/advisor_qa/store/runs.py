"""Runs storage: per-split JSONL of OpenTelemetry traces, manifest.

Layout under one dataset folder (decisions.md, 2026-11-20 and 2026-11-23):

    runs/{train,val,test}.jsonl      one OTLP/JSON TracesData per line (one run: root
                                     invoke_agent span and its children), sorted by
                                     (start time, trace id). The 39-field RunTrace is derived
                                     from it by advisor_qa.qa.view; it is not stored.
    labels/{train,val,test}.jsonl    hidden labels keyed by run_id = trace id
                                     (advisor_qa.store.labels; evaluation only)
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

from advisor_qa.otel.model import TracesData

SCHEMA_VERSION = "1"
SPLITS = ("train", "val", "test")
RUNS_DIR, LABELS_DIR = "runs", "labels"
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


def write_runs(root: Path, split: str, traces: Iterable[TracesData]) -> int:
    lines = (canonical_json(t.model_dump(mode="json", exclude_none=True)) for t in traces)
    return write_jsonl(root / RUNS_DIR / f"{split}.jsonl", lines)


def read_runs(root: Path, split: str) -> Iterator[TracesData]:
    for raw in read_jsonl(root / RUNS_DIR / f"{split}.jsonl"):
        yield TracesData.model_validate(raw)


def write_manifest(root: Path, manifest: Manifest) -> None:
    (root / MANIFEST_FILE).write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_manifest(root: Path) -> Manifest:
    return Manifest.model_validate_json((root / MANIFEST_FILE).read_text(encoding="utf-8"))
