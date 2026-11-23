# When Human Oversight of AI Fails

Columbia MSc Data Science capstone with LPL Financial. We build a QA and telemetry layer for an AI
wealth-management advisor assistant. With no real agent traces available, we **synthesize** about
10,000 runs, each with a full trace and a hidden error label, then compare random review with
risk-based review at equal capacity K.

**Current status:** the full pipeline runs end to end: households, synthetic prices, ground
truth, questions, mock tools, a correct agent, all 31 injected failure modes with hidden labels,
deterministic checks, a weighted risk score and the random-vs-risk evaluation at capacity K.
Failure rates and the severity formula are placeholders. **Start with `docs/roadmap.md`**, then
`docs/decisions.md` and `docs/open_questions.md`.

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
uv run python -m advisor_qa.world.build --n 50 --out data/synthetic/v0 --scf-path <path to SCFP2025.csv or .xlsx>
```

Then the prices, the runs and the evaluation (see `docs/roadmap.md`, "Run everything"):

```
uv run python -m advisor_qa.world.prices --out data/synthetic/prices
uv run python -m advisor_qa.pipeline.generate --households data/synthetic/v0 --out data/synthetic/runs
uv run python -m advisor_qa.eval.evaluate --runs data/synthetic/runs --split test
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
| `src/advisor_qa/world/` | Households, accounts, holdings, instruments, calendar, synthetic prices, household split |
| `src/advisor_qa/truth/` | Ground-truth portfolio impact (reads latent prices) |
| `src/advisor_qa/pipeline/` | Questions, mock tools, the correct agent, the dataset generator |
| `src/advisor_qa/inject/` | The 31 failure modes (30 from the client workbook + T1) and the hidden labels |
| `src/advisor_qa/store/` | Per-split JSONL runs, answers and labels; manifest |
| `src/advisor_qa/derive.py` | Answer fields and prompt parsing (shared by generator and checks) |
| `src/advisor_qa/checks/`, `scoring/` | Deterministic checks on a trace; the risk score |
| `src/advisor_qa/eval/` | Random vs risk-based review at capacity K (the only reader of labels) |
| `config/` | YAML settings (seeds, sizes, splits) |
| `data/` | Reference tables (committed) and generated data (gitignored); see `data/README.md` |
| `tests/` | Unit and integration tests; `tests/fixtures/` holds the client household fixture |
| `docs/` | `roadmap.md` (status, split of work), `decisions.md`, `open_questions.md` |
| `dashboard/` | Earlier prototype dashboard on placeholder data. Untouched until we have findings. |

Hidden labels are evaluation-only. Checks and scoring never reach `advisor_qa.schema.labels`, the
latent prices, the truth function or the injectors, even through other imports
(`tests/unit/test_separation.py` enforces it).
