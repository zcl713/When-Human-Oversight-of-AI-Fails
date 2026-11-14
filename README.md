# When Human Oversight of AI Fails

Columbia MSc Data Science capstone with LPL Financial. We build a QA and telemetry layer for an AI
wealth-management advisor assistant. With no real agent traces available, we **synthesize** about
10,000 runs, each with a full trace and a hidden error label, then compare random review with
risk-based review at equal capacity K.

**Current status:** the household layer (stage 1) is done. Prices, ground truth, agent and traces,
error injection, checks and evaluation are not. **Start with `docs/roadmap.md`** (status and the
proposed split of work), then `docs/decisions.md` and `docs/open_questions.md`.

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

Build the household tables (about 50 households, fixture included; needs the SCF extract, see
`data/README.md`):

```
uv run python -m advisor_qa.world.build --n 50 --out data/synthetic/v0 --scf-path <path to SCFP2025.xlsx>
```

Troubleshooting:
- `uv` hardlink error: the repo is in a OneDrive (or other cloud-synced) folder. Move it out, or set
  `UV_LINK_MODE=copy`.
- After moving the repo, delete `.venv` and run `uv sync` again.
- Parquet (`pyarrow`) fails to load on machines whose Windows application control policy blocks the
  library, so the household tables are CSV.

Add a dependency with `uv add <package>` (or `uv add --dev <package>`) and commit `pyproject.toml`
and `uv.lock`.

## Layout

| Path | What it is |
|---|---|
| `src/advisor_qa/schema/` | Pydantic models: the run trace and the separate hidden label |
| `src/advisor_qa/world/` | Households, accounts, holdings, instruments (done); calendar and prices (not started) |
| `src/advisor_qa/truth/` | Ground-truth portfolio impact (stage 2) |
| `src/advisor_qa/pipeline/` | Correct agent, mock tools, clean traces (stage 3) |
| `src/advisor_qa/inject/` | Error injection and hidden labels (stage 4) |
| `config/` | YAML settings (seeds, sizes, splits) |
| `data/` | Reference tables (committed) and generated data (gitignored); see `data/README.md` |
| `tests/` | Unit and integration tests; `tests/fixtures/` holds the client household fixture |
| `docs/` | `roadmap.md` (status, split of work), `decisions.md`, `open_questions.md` |
| `dashboard/` | Earlier prototype dashboard on placeholder data. Untouched until we have findings. |

Hidden labels are evaluation-only. Anything that later reads traces to flag or score them must never
import `advisor_qa.schema.labels`.
