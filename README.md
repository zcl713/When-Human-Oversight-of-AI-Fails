# When Human Oversight of AI Fails

Columbia MSc Data Science capstone with LPL Financial. We build a QA and telemetry layer for an AI
wealth-management advisor assistant. With no real agent traces available, we **synthesize** about
10,000 runs, each with a full trace and a hidden error label, then compare random review with
risk-based review at equal capacity K.

**Current status:** only the data synthesis pipeline is scaffolded. Checks, risk scoring and
evaluation come later. See `docs/decisions.md` and `docs/open_questions.md`.

## Set up (Windows, macOS, Linux)

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/):
   - macOS: `brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows: `winget install --id=astral-sh.uv -e` or
     `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
2. Clone the repo and, from its root, run:
   ```
   uv sync
   ```
   uv installs the right Python (3.11, from `.python-version`) and all dependencies into `.venv/`.
   You never need to activate the environment: prefix commands with `uv run`.
3. Check that it works:
   ```
   uv run poe test
   ```

## Everyday commands

| Command | What it does |
|---|---|
| `uv run poe test` | Run the tests (pytest) |
| `uv run poe lint` | Lint (ruff) |
| `uv run poe format` | Auto-format (ruff) |
| `uv run poe typecheck` | Type-check (mypy, strict) |

Add a dependency with `uv add <package>` (or `uv add --dev <package>`) and commit `pyproject.toml`
and `uv.lock`.

## Layout

| Path | What it is |
|---|---|
| `src/advisor_qa/schema/` | Pydantic models: the run trace and the separate hidden label |
| `src/advisor_qa/world/` | Households, instruments, calendar, prices (stage 1) |
| `src/advisor_qa/truth/` | Ground-truth portfolio impact (stage 2) |
| `src/advisor_qa/pipeline/` | Correct agent, mock tools, clean traces (stage 3) |
| `src/advisor_qa/inject/` | Error injection and hidden labels (stage 4) |
| `config/` | YAML settings (seeds, sizes, splits) |
| `data/` | Reference tables (committed) and generated data (gitignored); see `data/README.md` |
| `tests/` | Unit and integration tests; `tests/fixtures/` holds the client household fixture |
| `docs/` | Decisions log and open questions |
| `dashboard/` | Earlier prototype dashboard on placeholder data. Untouched until we have findings. |

Hidden labels are evaluation-only. Anything that later reads traces to flag or score them must never
import `advisor_qa.schema.labels`.
