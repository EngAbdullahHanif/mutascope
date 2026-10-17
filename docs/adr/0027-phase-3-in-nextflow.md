# 0027 — Phase 3 in Nextflow, with a release check

- Status: accepted
- Date: 2026-10-17
- Builds on: ADR 0007 (pipeline wiring, test profile), ADR 0018 (landscapes in the cache), ADR
  0020 (feature store), ADR 0022 (parallel `train cv`), ADR 0024 (`train calibrate`), ADR 0025
  (`train final` and its checks), ADR 0026 (weak point 4)

## Context

ADR 0026 listed as weak point 4: "`make pipeline` reproduces none of Phase 3". `data folds`,
`features embed`, `features build` and every `train` command had been run by hand. The committed
results and the deployed bundle came from those runs, and only their MLflow records said how.
Phase 4 builds the export, the API and the image on that bundle, so the pipeline had to rebuild
it, and something had to show that the rebuild gives the same thing.

`train final` already has a release gate (ADR 0025): it writes no bundle unless the CV+ refit
reproduces `train calibrate`'s rows (≤ 1e-9) and the out-of-fold Spearman reproduces `train
cv`'s (≤ 1e-6). In a pipeline that reruns all three steps, that gate only shows that one run is
consistent with itself. It cannot see a change that moves `train cv`, `train calibrate` and
`train final` together, such as a different feature store or a changed penalty grid.

Two constraints. Every landscape row and the manifest carry `code_version` (the git commit) and
`config_sha256` (the hash of the resolved config, which holds absolute paths), so files from
another commit or directory can never be byte-identical. And LightGBM and the MLP cost hours
(LightGBM `seq_struct`: 8.9 h; ADR 0022), too much for a default run.

## Decision

### 1. Six steps after the landscapes, in one chain

```
CLEAN -> FOLDS
CHECK_LANDSCAPES -> EMBED (per feature model) -> FEATURES_BUILD
FOLDS, FEATURES_BUILD -> TRAIN_CV (ridge seq_struct) -> TRAIN_CALIBRATE -> TRAIN_FINAL
                      -> CHECK_RELEASE -> [TRAIN_CV_EXTRA, params.extra_models]
```

Each process calls one CLI command, as before. `DOWNLOAD` now also links ProteinGym's CV fold
directory into the staged layout (`folds/`). `SUBSET` does not copy it, because `data folds` reads
only the processed assays' files. `EMBED` waits for `CHECK_LANDSCAPES`, so the GPU runs one model
at a time, and its cache lives under `data/`, outside the work dir, like the log-probability
cache (a rerun computes nothing that is cached). `FEATURES_BUILD` reads the landscapes only after
`CHECK_LANDSCAPES` has passed. The pipeline therefore refuses `params.supervised` without
`params.landscapes`, and refuses feature models that `params.models` does not score.

The training steps run strictly one after another. Each one uses every core (`--jobs`, default
all but one), and two at once would oversubscribe memory (ADR 0022). `TRAIN_CALIBRATE` writes to
its work dir and never resumes from earlier rows, so `train final`'s refit check tests this run's
calibration and not one left behind by a previous run. `params.final_run` names the deployed run
(`[ridge, seq_struct]`). It must match `train.final` in the config, and `train final` fails if
the cv results or calibration rows it gets belong to another run.

### 2. A release check before anything is published

New command, `mutascope train check-release` (`models/release.py`). It compares this run's
outputs with the ones the run would replace: `cv_ridge_seq_struct.parquet` and its
hyperparameters, `final_checks.parquet`, `final_manifest.parquet`, the calibration rows, and
every file of the landscape bundle. The files must be the same, and every table must have the
same columns, dtypes and rows. Every value must be equal to the bit: numbers are compared as raw
bytes, so `-0.0 ≠ 0.0` and a 1-ulp difference fails.

Only the provenance stamps in `train.release.ignore` (`code_version`, `config_sha256`) are left
out. They must still be present on both sides. They record how a file was made, not what it
holds, and any rerun changes them. The data-manifest hash (`manifest_sha256`) is *not* ignored,
because it fingerprints the input data.

`CHECK_RELEASE` is the only process that publishes the deployed run's outputs, and it publishes
them only after the check passes. A rerun that differs fails the pipeline and leaves the released
files as they were, which is the same rule `train final` applies to its own bundle. It publishes
per model label (`calibration/ridge_seq_struct`, `supervised_landscapes/ridge_seq_struct`) and
per file into `results/supervised/`, because a Nextflow copy replaces a target directory whole,
and `results/supervised/` holds every other run's files. `params.release_check = false`
publishes unchecked. That is for a deliberate model change, and for the test profile, which has
no release to compare with.

### 3. LightGBM and MLP behind a flag

`params.extra_models` (default `false`) adds `TRAIN_CV_EXTRA` for `params.extra_runs`: `lgbm`
and `lgbm_pca` on `seq_struct`, `lgbm_pca` on `seq`, `mlp_mse` and `mlp_rank` on `seq_struct`,
which are the committed rungs of ADR 0025's table. They run after the release check, one at a
time, and are published as they finish, unchecked. They are not deployed, and nothing has shown
that the MLP is bit-reproducible on MPS. The post-hoc `seq_struct_noemb` run is not among them.

### 4. Test profile: the same ridge path on the same two assays

The smoke test keeps ADR 0007's assays (AICDA, B2L11) and runs every Phase 3 step on them. It
needs two overrides, of the same kind the profile already has for a 2-assay subset:

- `features=smoke` (`conf/features/smoke.yaml`): only `seq_struct`, with `esm2_8m`, the model
  the smoke test scores, in place of ESM-2 650M and SaProt. The other sets each name a 650M
  model and are left out. Downloading and running two 650M models on a CI runner would cost
  more than the whole smoke test.
- `train.final.min_variants=100`: the two assays have 209 and 170 measured variants, below the
  deployed minimum of 300, so `train final` would have nothing to fit. (Swapping in two eligible
  assays would have lost AICDA's PDB `TITLE` record, which the smoke test exists to cover.)

`make pipeline-test` now also deletes the smoke test's embedding cache, so every run uses the
model.

### 5. Re-baseline the ridge CV results once, with the current code

The first default run failed its release check on two files and published nothing. The
calibration rows (213 files) and the whole bundle (202 files) were bit-identical. The
committed `cv_ridge_seq_struct.parquet` was not:

| Values | Differ | Max abs difference |
|---|---:|---:|
| Per-fold Spearman | 0 of 3,189 | 0 |
| Pooled Spearman | 3 of 639 | 1.2e-7 |
| Per-fold MSE | 2,416 of 3,189 | 5.8e-8 |
| Pooled MSE | 503 of 639 | 5.7e-9 |
| Selected penalties (hyperparameters file) | 0 of 3,189 | 0 (byte-identical file) |

`final_checks.parquet` differed in the same 3 pooled Spearmans, which it copies as
`cv_spearman_contiguous` (HMDH_HUMAN_Jiang_2019, OXDA_RHOTO_Vanella_2023_expression,
SC6A4_HUMAN_Young_2021). The committed file came from the serial run of 2026-10-14, made on
uncommitted code (`598c6ad…-dirty`) before `train cv --jobs` existed, where macOS's Accelerate
used several threads. Since PR #26, every path runs with one BLAS thread (ADR 0026, problem 1),
which adds floats in a different order. The current code cannot reproduce those last bits, and
no rounded number changes.

Decision (the author chose this over a per-file tolerance, which would give up "bit-identical"):
replace the committed `cv_ridge_seq_struct.parquet` and `final_checks.parquet` with this run's,
and regenerate everything computed from them into the same files, so every committed table is
again exactly what the committed CV files produce. `train compare` (`supervised_summary`,
`supervised_comparisons`) and `train final-table` (`final_summary`, `final_comparisons`,
`final_assay_differences`, `final_size_comparisons`) moved by at most 1.5e-8 (1.2e-7 in
`final_assay_differences`, which holds the per-assay values). `train leaderboard` did not move.
In the two final SVG figures, one coordinate changed in its sixth decimal; the PNGs are
byte-identical. A rerun of the pipeline with `-resume` then passed the check and published.
This is a one-off. The baseline is now the output of the code that is checked against it.

## Evidence

- *Smoke test* (`-profile local,test --extra_models true`, M4 Pro): 22 tasks, 55 s wall. `train
  final`: 2 eligible assays, 7,524 substitutions, CV+ refit max difference 0, out-of-fold
  Spearman against `train cv` max difference 0 in all three schemes. The extra LightGBM-PCA and
  MLP runs completed on both assays (MLP minimum lowered to 100 in the profile).
- *Default profile* (`make pipeline`, M4 Pro, warm caches): 33 tasks, 2 h 07 min wall. The
  Phase 1 and 2 steps, both embeddings and the feature store took 3 min. All scoring came from
  the cache, and no tracked result changed, only the run reports' `seconds` and
  `*_computed` columns. `train cv` took 1,878 s (13 workers), `train calibrate` 3,807 s and
  `train final` 1,748 s. `train final`: 198 eligible assays, 1,379,989 substitutions, CV+ refit
  max difference 0, out-of-fold Spearman against `train cv` 0 / 3.5e-7 / 1.2e-7 (random /
  modulo / contiguous). The release check then failed as described in section 5. After the
  re-baseline, `-resume` reran only `CHECK_RELEASE`: "PASS: ridge_seq_struct reproduces the
  release bit for bit (4 results files, 213 calibration files, 202 bundle files)". Checksums
  taken before and after: calibration rows, folds, `fits.parquet` and `coefficients.parquet`
  byte-identical. The 198 landscape partitions, `manifest.parquet` and `version.json` changed
  only in their stamps (`code_version` `96559b2`).
- *Release check against itself:* the committed files compared with themselves pass (4 results
  files, 213 calibration files, 202 bundle files, 2.3 s). Unit tests: a 1-ulp change, `-0.0`
  against `0.0`, a dtype, column order, row count, string and nullable-boolean change, a missing
  file and an extra file all fail with the cause named; changed stamps alone pass.
- *Standalone landscape check:* `mutascope score check-landscapes` on the real cache passes, with
  882 of 882 landscapes complete (ESM-2 35M, 150M and 650M 222 each on 217 assays, SaProt 216 on
  213). The coverage table equals the committed `results/data/landscape_coverage.parquet`.

## Consequences

- `make pipeline` now rebuilds the deployed model from raw data and fails if the rebuild differs
  from the release, so a later change that moves any deployed number cannot go unnoticed.
- The default run takes about 2 h with warm caches: `train calibrate` about an hour, `train cv`
  and `train final` about half an hour each. The zero-shot steps cost minutes.
- The provenance stamps of the published files change on every rerun. After `make pipeline`,
  `results/supervised/final_manifest.parquet` shows as modified in git, with only
  `code_version` and `config_sha256` changed. Commit it or restore it. This PR commits it, so
  its stamps match the republished bundle's. The Hub copies keep the stamps of the run that
  produced them, and their values are the same. The tracked run reports
  (`embedding_run_*`, `landscape_run_*`) are rewritten to say this run computed nothing new, as
  `structure_report.md` already was.
- Changing the deployed model now takes `--release_check false`, said explicitly, and the new
  files are then the release the next run is checked against.
- CI still trains nothing real at full size. The smoke test runs the real commands on 2 assays
  with a 30 MB model, and the full reproduction runs on the dev machine.
- The `docker` profile is still open, and waits for this phase's image.
