# 0001 — Tooling baseline

- Status: accepted
- Date: 2026-10-09

## Context

MutaScope is a public portfolio project where reproducibility and code quality matter more than
leaderboard points. Development happens on an Apple Silicon Mac (MPS), while deployment targets
linux/amd64 (Hugging Face Spaces) and CI runs on GitHub Actions.

## Decision

- **uv** manages Python 3.11 and all dependencies; `pyproject.toml` + `uv.lock` are the single source.
  Heavy ML dependencies (torch, Hugging Face `transformers` for ESM-2, …) are added only when the code
  that needs them lands.
- **src layout** (`src/mutascope/`) so tests run against the installed package, not the working tree.
- **ruff** for lint and format (line length 100); `T20` enforces "no `print`, use structlog".
- **pyright strict** over `src/`, `tests/` and `api/`.
- **pre-commit** runs ruff and pyright from the uv env, so hook versions follow `uv.lock`.
- **pytest**, with a `slow` marker excluded by default; slow tests (real 650M models) run via `make test-slow`.
- **Typer** CLI as the single entry point; **Nextflow DSL2** orchestrates it with `local`, `docker`
  and `test` profiles. `local` puts `.venv/bin` on `PATH` because Docker on macOS has no MPS.
- **Hydra** for configuration under `conf/`.
- **Makefile** holds the canonical commands.

## Consequences

- One `make setup` gives a working environment; CI can mirror the same commands.
- Nextflow needs Java 17+ on the host; Docker is needed only for the `docker` profile.
- pyright strict will require typed wrappers or stubs for untyped scientific libraries as they are added.
