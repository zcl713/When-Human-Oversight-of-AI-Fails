"""Checks and scoring never reach the hidden labels, latent prices, the truth function or the
injectors, directly or through anything they import."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
PKG = SRC / "advisor_qa"
GUARDED = ["advisor_qa.checks", "advisor_qa.scoring", "advisor_qa.qa"]
FORBIDDEN = [
    "advisor_qa.schema.labels",
    "advisor_qa.store.labels",
    "advisor_qa.world.latent",
    "advisor_qa.truth",
    "advisor_qa.inject",
    "advisor_qa.eval",
]


def _file(module: str) -> Path | None:
    base = SRC / Path(*module.split("."))
    for f in (base.with_suffix(".py"), base / "__init__.py"):
        if f.exists():
            return f
    return None


def _imports(module: str) -> set[str]:
    f = _file(module)
    if f is None:
        return set()
    pkg = module if f.name == "__init__.py" else module.rsplit(".", 1)[0]
    out: set[str] = set()
    for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = pkg.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                mod = f"{base}.{node.module}" if node.module else base
            else:
                mod = node.module or ""
            out.add(mod)
            out |= {f"{mod}.{a.name}" for a in node.names if _file(f"{mod}.{a.name}")}
    # importing a.b.c runs a/__init__ and a/b/__init__ too
    parents = {".".join(m.split(".")[:i]) for m in out for i in range(1, m.count(".") + 1)}
    return {m for m in out | parents if m.startswith("advisor_qa")}


def closure(module: str) -> set[str]:
    seen: set[str] = set()
    todo = [module]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        todo += sorted(_imports(m) - seen)
    return seen


def _modules(package: str) -> list[str]:
    root = PKG / package.split(".", 1)[1]
    return sorted(
        ".".join(f.relative_to(SRC).with_suffix("").parts).removesuffix(".__init__")
        for f in root.rglob("*.py")
    )


def test_checks_and_scoring_never_reach_labels_or_truth() -> None:
    for pkg in GUARDED:
        mods = _modules(pkg)
        assert mods, pkg
        for m in mods:
            reached = closure(m)
            bad = sorted(r for r in reached for f in FORBIDDEN if r == f or r.startswith(f + "."))
            assert not bad, f"{m} reaches {bad}"


def test_checks_never_import_label_schema_directly() -> None:
    for pkg in GUARDED:
        for m in _modules(pkg):
            assert "advisor_qa.schema.labels" not in _imports(m), m


def test_detector_follows_imports() -> None:
    assert "advisor_qa.schema.labels" in closure("advisor_qa.store.labels")
    assert "advisor_qa.world.latent" in closure("advisor_qa.truth.impact")
    assert "advisor_qa.schema.labels" in closure("advisor_qa.inject.plan")
