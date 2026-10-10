# 0007 — Pipeline wiring, test profile and CI

- Status: accepted
- Date: 2026-10-10

## Context

Phase 1's CLI commands (download, check-variants, check-structures, clean, structure-features,
foldseek-3di, structure-report) existed but were run by hand. The Nextflow pipeline had to chain
them end to end, and a `test` profile had to run the same steps fast enough for CI. Constraints:
the full release is about 2 GB (1.9 GB of it the zero-shot scores archive, which no Phase 1 step
reads). `check-structures` fails on any `structure_exceptions` entry missing from the reference
(ADR 0003). Typer rejects arguments a command does not declare. And no Docker image exists until
Phase 4.

## Decision

**Download into a cache, link into the pipeline.** `DOWNLOAD` runs `mutascope data download`
with `data.data_dir=${params.data_dir}` (default: the repository's `data/`, which holds the
committed `manifest.yaml`). The command is idempotent and re-verifies every cached file, so a
rerun hashes about 75 MB instead of downloading 2 GB. With `--link-dir`, it then symlinks a fixed
layout into the task directory:

```
DMS_substitutions.csv   dms/   structures/
```

`mutascope.data.staging` defines this layout once, and `RemoteFile.extract_dirname` stays the
only place that knows how archive directories are named. The links are made only after
verification succeeds.

**Test assays are named, not chosen by a rule.** The `test` profile sets `params.assays` to
`AICDA_HUMAN_Gajula_2014_3cycles` and `B2L11_HUMAN_Dutta_2010_binding-Mcl-1`, and `SUBSET` copies
them into the same layout, so every later step runs unchanged. Both are 198 residues long with
209 and 170 single mutants, and they cover two function types (Activity, Binding). AICDA's PDB
has a `TITLE` record, which is the case that broke 3Di parsing in build log entry 5. The rule
the earlier `max_assays = 2` placeholder implied, "the N smallest assays", would have picked
NPC1_HUMAN first (63 variants, but 1,278 residues), and its choice would change whenever the
reference did.

**One override list reaches every command.** Every `data` command accepts `--config-dir` and Hydra
overrides, including `check-variants`, `clean` and `subset`, which read nothing from the config.
They still compose it, so a misspelt override fails instead of being ignored. A test checks that
every `data` command has these parameters. The `test` profile's overrides are:

- `data.structure_exceptions=null`: the 4 documented exceptions are not in a 2-assay subset.
- `~data.files.zero_shot_scores`: skips the 1.9 GB archive.

**Validation gates the outputs.** `VALIDATE` (check-variants, then check-structures) emits
`validation.txt`, and `CLEAN`, `STRUCT_FEATURES` and `FOLDSEEK_3DI` take it as an input, so none
of them starts unless validation passed. `STRUCTURE_REPORT` runs last as the cross-check.
Outputs are published by copy (`data/processed/<table>/`, `results/data/`). The `test` profile
publishes to the gitignored `pipeline/results/` instead, so it never overwrites real outputs.

**CI runs the test profile natively.** `make pipeline-test` is `-profile local,test`: the `uv`
environment on the runner, Foldseek's linux-avx2 release binary checked against its sha256, and
the downloaded files cached with a key of `conf/data/proteingym.yaml` + `data/manifest.yaml`.
The `docker,test` smoke run from CLAUDE.md returns once the image exists (Phase 4). The `docker`
profile will also need `params.data_dir` mounted, because `DOWNLOAD` links into it.

## Consequences

- `make pipeline` reproduces the hand-run outputs exactly. All 217 variant partitions and all 213
  structure-feature and 3Di partitions are byte-identical, and so are the committed reports. The
  run takes 42 s on the M4 Pro when the download is already cached.
- `make pipeline-test` takes 32 s from an empty cache (about 75 MB downloaded) and 5 s when cached.
- The pipeline, like the CLI, needs Foldseek `10-941cd33` (`FOLDSEEK_BIN` or `PATH`).
- Until Phase 4, CI does not exercise the container. That gap is accepted, because the image
  will be built for the API, and its pipeline use is tested when it lands.

## Alternatives rejected

- **Download into the Nextflow work directory.** This is the conventional pattern, but every
  non-`-resume` `make pipeline` would re-download 2 GB, and the committed manifest would have to
  be staged in and copied back.
- **A `config show` command that prints the resolved raw dir for the shell to link.** This puts
  path logic in `.nf` scripts. `--link-dir` keeps it in Python, where it is tested.
- **Building a pipeline Docker image now.** It is out of scope for Phase 1, and the amd64 image on
  an arm64 Mac runs the AVX2 Foldseek binary under emulation, which would make the local smoke
  test differ from CI.
