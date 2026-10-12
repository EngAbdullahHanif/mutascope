# MutaScope

Predicts the functional effect of every single amino-acid substitution in a protein and presents it as an
interactive mutation landscape, with an honest benchmark on ProteinGym DMS assays.

Status: early development (Phase 2, zero-shot models and benchmark harness). See [docs/ROADMAP.md](docs/ROADMAP.md).

## Quick start

```bash
make setup   # uv sync --all-extras && pre-commit install
make lint test
make pipeline
```

Requires [uv](https://docs.astral.sh/uv/), Nextflow (Java 17+) and Foldseek (see below).

## Running the pipeline

`pipeline/main.nf` chains the `mutascope` commands
([ADR 0007](docs/adr/0007-pipeline-wiring-and-ci.md)):

```
DOWNLOAD -> [SUBSET] -> VALIDATE -> CLEAN, STRUCT_FEATURES, FOLDSEEK_3DI -> STRUCTURE_REPORT
                                    CLEAN -> SCORE_ZERO_SHOT
```

- `make pipeline` runs on all 217 assays. The release is downloaded once into `data/raw/` and
  re-verified against `data/manifest.yaml` on every run. Outputs go to
  `data/processed/{variants,structure_features,three_di}/`, and reports (validation, cleaning,
  structure) go to `results/data/`.
- `SCORE_ZERO_SHOT` scores every assay's single mutants with ESM-2 650M (`--model esm2_150m`
  picks another `conf/model/` group) and writes `data/processed/zero_shot/<model>/`. It scores
  only the positions each assay measures and is the long step: about 1.5 hours on an M4 Pro.
  On a laptop, run it as `caffeinate -ims make pipeline` so the machine does not sleep.
- `make pipeline-test` runs the same steps on 2 small assays, with the 30 MB `esm2_t6_8M`
  model, and skips the 1.9 GB zero-shot archive. It writes only to `pipeline/results/`
  (gitignored), taking about 30 s from an empty cache. CI runs it on every push.

### Resuming an interrupted scoring run

Run the same command again. Every scored sequence is written to `data/cache/logprobs/` as soon
as it is finished, and that directory is outside Nextflow's work directory, so a rerun (with or
without `-resume`) reads finished assays from disk and runs the model only for the rest. At most
the one sequence in progress is lost, which is under 7 minutes of work. The command prints how
many assays came from the cache, and `results/data/zero_shot_run_<model>.parquet` records
`positions_computed` per assay (0 = read from the cache)
([ADR 0013](docs/adr/0013-benchmark-scoring-run.md)).

### The model gate

```bash
uv run mutascope eval model-gate \
    --scores-dir data/processed/zero_shot/esm2_650m \
    --precomputed-dir data/raw/proteingym_v1.3/zero_shot_substitutions_scores \
    --reference data/raw/proteingym_v1.3/DMS_substitutions.csv --out-dir results
```

compares our scores with ProteinGym's precomputed `ESM2_650M` on the 696,311 single mutants
(ADR 0010) and exits 1 if any assay has per-variant Spearman below 0.99 or the aggregate differs
by more than 0.01. `make test-slow` runs the same check as a test.

Hydra overrides reach every command through `--hydra_overrides`, for example
`nextflow run pipeline/main.nf -profile local --hydra_overrides 'structure.foldseek.threads=8'`.

## Exploring the data

```bash
uv run marimo edit notebooks/eda.py
```

The notebook shows assay sizes, function types, score distributions and sequence lengths for
the processed data (run `make pipeline` first). All of its tables and charts come from
`mutascope.eda`. [docs/DATA_CARD.md](docs/DATA_CARD.md) describes the dataset.

## Benchmark harness

`mutascope eval` scores predictions per assay (Spearman, AUC, MCC, NDCG@10%, top-10% recall)
and aggregates them as ProteinGym does: per UniProt ID and function type, then per function type,
then overall, with bootstrap 95% CIs over assays
([ADR 0011](docs/adr/0011-benchmark-harness.md)). Run it on ProteinGym's own precomputed scores
to check the harness against the published leaderboard:

```bash
R=data/raw/proteingym_v1.3
uv run mutascope eval precomputed --scores-dir $R/zero_shot_substitutions_scores \
    --reference $R/DMS_substitutions.csv --rows all --out-dir results
uv run mutascope eval compare-leaderboard --results results/proteingym_precomputed_all.parquet
```

`--rows all` scores single and multi-mutants, as the leaderboard does; `--rows single` scores
the 696,311 single mutants MutaScope uses. The harness gate in `tests/test_reproduction.py` runs
the same comparison as a test whenever the release is downloaded.

## Publishing the dataset

The processed dataset is on the Hugging Face Hub as
[`engAbdullahHanif/mutascope-proteingym-v1.3`](https://huggingface.co/datasets/engAbdullahHanif/mutascope-proteingym-v1.3)
(`publish.repo_id` in `conf/publish/hub.yaml`).

`uv run mutascope data publish` stages the Hub copy in `build/hf_dataset/` (Parquet tables,
reports, card, `provenance.json`, `SHA256SUMS`) without uploading anything. To upload it, with a
write token in `HF_TOKEN`:

```bash
uv run mutascope data publish --push
```

A new repo is created private; an existing one keeps its visibility
([ADR 0008](docs/adr/0008-eda-and-hub-publication.md)).

## Installing Foldseek

`mutascope data foldseek-3di` needs [Foldseek](https://github.com/steineggerlab/foldseek) release
**`10-941cd33`** exactly: 3Di tokens depend on the build, so any other version is refused
([ADR 0006](docs/adr/0006-structure-features.md)). Homebrew has no `foldseek` formula (checked
2026-10-10), so install the official release binary:

```bash
# macOS (universal). On Linux/amd64 (CI, Docker) use foldseek-linux-avx2.tar.gz instead.
curl -LO https://github.com/steineggerlab/foldseek/releases/download/10-941cd33/foldseek-osx-universal.tar.gz
shasum -a 256 foldseek-osx-universal.tar.gz
# expect 6d8b07e188d443044f0c98db30aa3d4cca6750e97b50679372b54b45a664cae5
tar xzf foldseek-osx-universal.tar.gz
sudo install -m 755 foldseek/bin/foldseek /usr/local/bin/foldseek   # any directory on PATH works
foldseek version   # must print 941cd33ff0771cd2e3f144e3293e22a2b87e9fda
```

The command finds the binary in this order:

1. `FOLDSEEK_BIN`, if set (for example `export FOLDSEEK_BIN=$HOME/tools/foldseek`). It must be an
   executable file.
2. Otherwise, `foldseek` on `PATH`.

If neither is found, the command fails before doing any work and points here. The structure
report (`results/data/structure_report.md`) records the binary it used by version, sha256 and
how it was found (`FOLDSEEK_BIN` or `PATH`), never by path, so the report is identical on any
machine. The full path is kept only in `<three_di dir>/foldseek_run.json`, under the gitignored
`data/`, for local debugging. The macOS binary
above has sha256 `1b446bbece6b01cf0a50b28419f353522204f44a10a5872462f8ad400d9aadab`. Without
Foldseek, `make test` still passes, because one golden test is skipped.

## License

MutaScope is released under the [MIT License](LICENSE). The processed dataset derived from
ProteinGym v1.3 is also MIT-licensed, matching ProteinGym's own licence. When you use specific
assays, please also cite ProteinGym and the original studies
([docs/DATA_CARD.md](docs/DATA_CARD.md)).
