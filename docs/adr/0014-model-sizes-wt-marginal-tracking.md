# 0014 — ESM-2 35M and 150M, wild-type marginals, and MLflow tracking

- Status: accepted
- Date: 2026-10-12 (decision 13 and the paired comparisons added the same day)
- Changes: ADR 0013, decision 7 (the model-gate command no longer writes benchmark results)

## Context

After ADR 0013 only ESM-2 650M had been scored, with the masked marginal. Phase 2 needs the two
smaller checkpoints (the ones the API will serve), the wild-type marginal for all three (the
API's fast mode, and the "fast vs masked" comparison), and a record of every run: which model
and revision produced which numbers, with which config.

Three things in the existing code stood in the way:

- **Names had no method in them.** Scores went to `zero_shot/<model>/`, the run report to
  `zero_shot_run_<model>.parquet` and results carried `model = <model>`. A wild-type run would
  have overwritten the masked run of the same checkpoint.
- **Only the model gate wrote benchmark results.** It needs ProteinGym's precomputed scores of
  the same model and method. ProteinGym's `ESM2_*` columns are masked marginals, so a wild-type
  score set could not be benchmarked at all.
- **Nothing recorded a run** except the files it wrote.

## Decision

1. **The method is part of a score set's name.** `eval.zero_shot.run_label` is the only place
   that builds it: the model name for the masked marginal (so existing 650M files keep their
   names), `<model>_wt` for the wild-type marginal. The label is the `model` column of scores
   and results, the score directory and the report and results file names. The results format
   (`model, assay_id, split, fold, metric, value`) is unchanged; a separate method column would
   have changed the schema every later figure reads.
2. **`mutascope score zero-shot --out-dir` is the root**, and the command writes
   `<root>/<label>/`. Nextflow therefore never builds a label itself.
3. **One writer of benchmark results: `mutascope eval zero-shot`.** It joins a score set with
   the labels in the processed variants, requires the two to hold exactly the same variants,
   and writes `zero_shot_<label>_single.parquet` and `..._summary.parquet`. It refuses a score
   set whose label is not the one the config and `--fast` name, so results cannot be tracked
   under the wrong revision. `eval model-gate` now writes only the agreement table.
4. **Labels come from the processed variants, not from ProteinGym's score files.** See
   "Labels" below: the two differ in the last digits, which moves the 650M aggregate by 1e-07.
5. **The model gate also runs for 35M and 150M**, against `ESM2_35M` and `ESM2_150M`, with the
   same limits (0.99 per assay, 0.01 on the aggregate). ADR 0010's gate remains the 650M one;
   the other two are extra evidence that the scorer, not one checkpoint, is right. Wild-type
   score sets are not gated: there is nothing published to compare them with. They are checked
   against our own masked scores instead (see Evidence).
6. **Tracking is MLflow with a local SQLite store.** `mlruns/mlflow.db` plus
   `mlruns/artifacts/`, gitignored, no server and no account. MLflow 3.17 refuses the plain
   `./mlruns` file store for new runs ("in maintenance mode"), so SQLite is the local backend.
   The experiment is created with an explicit artifact location; without one MLflow writes
   artifacts to `./mlruns` of whatever directory the command runs in, which under Nextflow is a
   work directory.
7. **One tracked run per CLI invocation** of `score zero-shot`, `eval zero-shot` and
   `eval model-gate`. A run has:
   - *params*: every key of the model config (name, Hub id, pinned revision, batch size,
     window), the method, the device setting and the ProteinGym release and commit;
   - *tags*: the step, the label, and `code_version` (the git commit, with `-dirty` if `src/`,
     `conf/`, `pipeline/`, `pyproject.toml` or `uv.lock` differ from it; result files a run
     rewrites do not count);
   - *metrics*: for a benchmark run the overall value and CI of all five metrics and the
     per-function-type values, copied from the summary the command just wrote; for a scoring
     run the counts, this invocation's time and the cache log's total over all invocations;
     for a gate run the two checks' numbers and a `gate_passed` tag;
   - *artifacts*: the full resolved config as `config.yaml` and the files the command wrote.
8. **Tracked metrics are never computed separately.** `summary_metrics` reads the summary
   frame. An undefined (NaN) aggregate is left out of the run and stays NaN in the file.
9. **A tracking failure fails the command, after its outputs are written.** No result is lost,
   and a run that was not tracked does not look like one that was. `tracking.enabled=false`
   turns tracking off.
10. **`mlflow` is an optional extra (`tracking`).** `make setup` and CI install every extra;
    the serving image (Phase 4) installs only `api`. It is imported only when a run is logged.
11. **The pipeline runs every (model, method) pair.** `params.models` × `params.methods`
    (default: three sizes × masked and wt) feed `SCORE_ZERO_SHOT`, then the new
    `BENCHMARK_ZERO_SHOT`. Both have `maxForks 1`: one GPU, and one SQLite file written by one
    task at a time. The model gate stays a command and a slow test, because it needs the
    1.9 GB precomputed archive that the test profile skips.
12. **Not tracked:** `eval precomputed`. Those are ProteinGym's scores; there is no model
    revision of ours to record.
13. **Model comparisons use a paired bootstrap.** `eval.compare.paired_bootstrap_diff(A, B)`
    draws one set of assays per resample (stratified by function type, like the unpaired CI),
    applies it to both score sets, recomputes the full ProteinGym aggregate of each and keeps
    `A - B`. It reports the difference of the two aggregates, its percentile CI and the share
    of resamples in which A is strictly better. The aggregate is a weighted mean of assays, so
    that difference is also the aggregate of the per-assay differences. An assay counts only if
    the metric is defined for both. The unpaired CIs answer "how well does this model do on
    assays like these"; most of their width is the spread between assays, which two models
    scored on the same assays share. Reading a comparison off two such intervals therefore
    hides real differences. Every figure or sentence that compares two score sets must use the
    paired interval. `mutascope eval compare` runs the pairs listed in `eval.comparisons` with
    the settings of `eval.bootstrap` (10,000 resamples, 95%, seed 0), each pair from the same
    seed, writes `results/zero_shot/comparisons.parquet` and logs one MLflow run.

## Evidence

**Runs** (M4 Pro, MPS; 217 assays, 696,311 single mutants, 187 cache entries per model and
method). Model time is the cache log's total.

| Model | Method | Positions scored | Model time |
|---|---|---:|---:|
| ESM-2 35M | masked | 37,753 | 1,119 s (19 min), in three invocations |
| ESM-2 150M | masked | 37,753 | 1,718 s (29 min) |
| ESM-2 650M | masked | 37,753 | 6,081 s (1 h 41 min; ADR 0013) |
| ESM-2 35M | wild-type | 62,671 | 7.7 s |
| ESM-2 150M | wild-type | 62,671 | 8.7 s |
| ESM-2 650M | wild-type | 62,671 | 24.5 s |

The wild-type marginal reads every residue from its one pass, so it fills all 62,671 residues
of the 187 entries, not only the 37,753 measured ones.

**Results** (ProteinGym v1.3, single mutants, zero-shot, ProteinGym-style aggregate; 95%
bootstrap CI for Spearman). AUC and MCC are defined on 216 assays.

| Score set | Spearman | 95% CI | AUC | MCC | NDCG@10% | Top-10% recall |
|---|---:|---|---:|---:|---:|---:|
| `esm2_35m` | 0.3113 | 0.285–0.338 | 0.6746 | 0.2399 | 0.7205 | 0.1733 |
| `esm2_150m` | 0.3820 | 0.358–0.408 | 0.7153 | 0.2950 | 0.7402 | 0.1845 |
| `esm2_650m` | 0.4125 | 0.389–0.441 | 0.7326 | 0.3253 | 0.7586 | 0.1946 |
| `esm2_35m_wt` | 0.3028 | 0.277–0.329 | 0.6688 | 0.2298 | 0.7163 | 0.1765 |
| `esm2_150m_wt` | 0.3772 | 0.354–0.403 | 0.7121 | 0.2924 | 0.7398 | 0.1868 |
| `esm2_650m_wt` | 0.4078 | 0.386–0.436 | 0.7295 | 0.3225 | 0.7557 | 0.1884 |

The wild-type marginal costs 0.005 to 0.009 Spearman against the masked marginal of the same
checkpoint. The separate CIs above overlap almost entirely, and they are the wrong tool for
that question: see "Paired comparisons" below.

**Model gate**, masked score sets against ProteinGym's precomputed columns:

| Model | Lowest per-assay Spearman | Largest score difference | Aggregate, ours / theirs |
|---|---:|---:|---|
| ESM-2 35M | 0.99999996 | 1.33e-04 | 0.3113 / 0.3113 |
| ESM-2 150M | 0.99999991 | 2.04e-04 | 0.3820 / 0.3820 |
| ESM-2 650M | 0.99999996 | 2.35e-04 | 0.4125 / 0.4125 |

**Wild-type against masked** (our own scores, same checkpoint, per assay): median Spearman
0.983 (35M), 0.986 (150M) and 0.985 (650M); lowest 0.930, 0.923 and 0.834
(`UBR5_HUMAN_Tsuboyama_2023_1I2T`, 650M). The wild-type marginal has the higher Spearman
against `DMS_score` in 52, 62 and 70 of the 217 assays.

**Paired comparisons** (`results/zero_shot/comparisons.parquet`; Spearman, overall, A − B,
95% paired bootstrap CI, 217 assays):

| A | B | A − B | 95% CI | A better in |
|---|---|---:|---|---:|
| `esm2_150m` | `esm2_35m` | +0.0707 | +0.0589 to +0.0840 | 100% of resamples |
| `esm2_650m` | `esm2_150m` | +0.0306 | +0.0225 to +0.0405 | 100% |
| `esm2_35m` | `esm2_35m_wt` | +0.0085 | +0.0061 to +0.0107 | 100% |
| `esm2_150m` | `esm2_150m_wt` | +0.0048 | +0.0022 to +0.0070 | 100% |
| `esm2_650m` | `esm2_650m_wt` | +0.0047 | +0.0007 to +0.0079 | 99.0% |

Each size step and each masked-versus-wild-type difference has a paired interval that excludes
zero. The unpaired intervals of the two score sets overlap in four of the five pairs (all but
150M against 35M). The paired interval of 650M masked against wild-type is 0.007 wide; the
unpaired interval of either score set is 0.05 wide.

The file also holds the other four metrics and the five function types. Not every difference
is clear there. For top-10% recall the wild-type marginal is not worse at 35M (−0.0032, CI
−0.0082 to +0.0033) or 150M (−0.0023, CI −0.0045 to +0.0004). By function type, 650M against
150M on Spearman is clear for Activity and OrganismalFitness and not for Binding (+0.028, CI
−0.002 to +0.059, 13 assays), Expression or Stability. These intervals are not corrected for
the number of comparisons.

**Labels.** ProteinGym's zero-shot score files repeat `DMS_score`, printed with different
rounding from the DMS files: 3,297 of the 696,311 single-mutant labels differ, in 82 assays, by
at most 3.7e-09. `DMS_score_bin` is identical. In `CCR5_HUMAN_Gill_2023` 122 labels differ by
at most 2.2e-16, which is enough to split ties: 955 distinct values in the score file, 1,014 in
the DMS file. That assay's 650M Spearman is 0.347414 with the score file's labels (the results
committed with ADR 0013) and 0.347421 with the processed variants (now). The aggregate moves
from 0.4125268 to 0.4125269. No other assay's Spearman, AUC, MCC or top recall changes; five
NDCG values move by 4e-16. The
processed variants are the dataset every supervised model will train on, so they are the label
source. The gate still compares both sides on the score file's labels, as before.

**Tracking.** The full run left 15 runs in the store: 6 scoring, 6 benchmark and 3 gate, all
with `code_version` `41bafb3`. `make mlflow` serves them.

## Consequences

- `results/` now holds `zero_shot_<label>_single*.parquet` for six score sets and
  `model_gate_<model>.parquet` for three. `results/zero_shot_esm2_650m_single*.parquet` were
  regenerated with labels from the processed variants (one assay's Spearman, 7e-06).
- A run report (`results/data/zero_shot_run_<label>.parquet`) describes one invocation. A rerun
  on a warm cache overwrites it with `positions_computed = 0`. The committed reports are those
  of the invocations that ran the model (for 35M, the last of three); the total over
  invocations is in the cache log and in the tracked run (`model_seconds_all_runs`).
- The MLflow store is local and not versioned. Deleting `mlruns/` loses the run history but no
  result; rerunning the commands on the warm cache recreates the runs in minutes.
- Task 6 figures that compare score sets read `results/zero_shot/comparisons.parquet`; a new
  pair is added to `eval.comparisons`. The command is not a Nextflow step yet: it needs every
  benchmark result at once, and joins the pipeline with the figures.
- `scoring.device` is logged as configured (`auto`), not as resolved (`mps`).
- On MPS, the 8M and 35M checkpoints made PyTorch's buffer cache grow without limit; the scorer
  now empties it after each sequence (build log entry 13). Do not run anything else on the GPU
  during a scoring run.
