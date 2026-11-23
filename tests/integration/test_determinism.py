import filecmp
from pathlib import Path

from advisor_qa.pipeline.generate import generate, write_dataset
from advisor_qa.store.labels import read_labels
from advisor_qa.store.runs import read_answers, read_manifest, read_runs
from advisor_qa.world.instruments import load_instruments
from tests.pipeline_world import config, prices, tables

FILES = [
    "manifest.json", "split_households.csv", "directory.csv",
    *[f"{d}/{s}.jsonl" for d in ("runs", "answers", "labels") for s in ("train", "val", "test")],
]  # fmt: skip


def _write(out: Path, seed_offset: int = 0) -> None:
    seed, pipe, inj = config()
    t = tables()
    ds = generate(t.households, t.accounts, t.holdings, load_instruments(), prices(), pipe, inj,
                  seed + seed_offset, max_days=4)  # fmt: skip
    assert not ds.unexplained
    write_dataset(ds, out, seed + seed_offset)


def test_same_seed_gives_same_dataset(tmp_path: Path) -> None:
    """Two generator runs with the same seed must produce identical runs and labels."""
    _write(tmp_path / "a")
    _write(tmp_path / "b")
    match, mismatch, errors = filecmp.cmpfiles(tmp_path / "a", tmp_path / "b", FILES, shallow=False)
    assert match == FILES and not mismatch and not errors

    runs = list(read_runs(tmp_path / "a", "train"))
    labels = read_labels(tmp_path / "a", "train")
    answers = read_answers(tmp_path / "a", "train")
    assert len(runs) == 4 * config()[1].questions_per_day  # first 4 days are all train dates
    assert {r.run_id for r in runs} == set(labels) == set(answers)
    assert read_manifest(tmp_path / "a").counts["train"] == len(runs)


def test_different_seed_gives_different_runs(tmp_path: Path) -> None:
    _write(tmp_path / "a")
    _write(tmp_path / "b", seed_offset=1)
    assert (tmp_path / "a/runs/train.jsonl").read_bytes() != (
        tmp_path / "b/runs/train.jsonl"
    ).read_bytes()
