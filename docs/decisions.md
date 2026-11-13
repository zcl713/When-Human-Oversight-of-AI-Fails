# Decisions log

## 2026-11-13: scope for version 1
- **Reviewer data is dropped.** No reviewer model, no Review field group, no H1-H8 tests, no pilot.
  Inspection is a pure selection comparison: at capacity K, which errors land in the reviewed set,
  assuming the reviewer catches what they see.
- **No model is trained in version 1.** Checks are deterministic code. The risk score is a swappable
  scorer; v1 is a weighted sum of flags. Logistic regression and gradient boosting are on hold.
- **Hidden labels are evaluation-only.** Stored separately from traces; never imported by checks or scoring.
- **A held-out test split is defined from the start** (by household and by date).

## 2026-11-13: repo layout
- Repo root is the pipeline project (`src/advisor_qa`). The earlier prototype dashboard lives in
  `dashboard/` on placeholder data and is untouched until we have findings.
- Only the data synthesis pipeline is scaffolded. Checks, scoring and evaluation are added when
  we design them; how risk is computed and which checks to build are not settled.
- Tooling: uv, ruff, pytest, mypy, poethepoet (no Makefile, so it works on Windows and macOS).
- Work happens directly on `main` while nobody else is working in the repo.
