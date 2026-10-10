# MutaScope

Predicts the functional effect of every single amino-acid substitution in a protein and presents it as an
interactive mutation landscape, with an honest benchmark on ProteinGym DMS assays.

Status: early development (Phase 1, foundations). See [docs/ROADMAP.md](docs/ROADMAP.md).

## Quick start

```bash
make setup   # uv sync --all-extras && pre-commit install
make lint test
make pipeline
```

Requires [uv](https://docs.astral.sh/uv/), Nextflow (Java 17+), [Foldseek](https://github.com/steineggerlab/foldseek)
release `10-941cd33` (for 3Di tokens; see [ADR 0006](docs/adr/0006-structure-features.md)) and, for
`make pipeline-test`, Docker.
