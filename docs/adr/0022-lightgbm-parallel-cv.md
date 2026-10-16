# 0022 — LightGBM with nested Optuna search, train-only PCA, SHAP, and parallel `train cv`

- Status: accepted
- Date: 2026-10-16
- Builds on: ADR 0019 (CV loop, canary), ADR 0020 (feature store), ADR 0021 (ridge rung, inner CV
  split like the outer scheme), ADR 0014 (paired bootstrap)

## Context

The second rung of the Phase 3 ladder is gradient-boosted trees on the store features, tuned with
Optuna inside the training folds, with and without PCA of the 1,280-column embeddings, explained
with SHAP and compared with ridge `seq_struct` and zero-shot on the same test folds. Ridge
`seq_struct` took 4,256 s serially; a tuned LightGBM is two to three orders of magnitude more
work per fit, so `train cv` first had to use all cores.

## Decision

### 1. `train cv --jobs N`: assays in worker processes, under a memory budget

Assays share nothing, so `cv_metrics_table` runs them through `utils.parallel.run_parallel`:
spawned processes (default: cores − 1 = 13), each with OpenMP and BLAS capped at one thread
(`OMP_NUM_THREADS`, `VECLIB_MAXIMUM_THREADS`, … set for the children only), largest assay first.
The output is sorted by assay, so it does not depend on `--jobs`: a test checks the serial and
parallel tables are equal, and model factories and designs are picklable (`functools.partial`,
`KeyDesign`, `StoreDesign`) because they are sent to the workers.

**Memory, not cores, is the limit.** The first parallel ridge run started the 13 largest assays
together; ridge peaks near 2 GB per worker on the largest (16,897 × 2,647 float64), the workers
needed ~26 GB on a 24 GB machine, and they sat at 3–17% CPU while the system swapped. Tasks now
carry a weight (variants) and share a budget, `train.max_variants_in_flight`; when the next large
assay does not fit, smaller ones behind it fill the free workers. 160,000 for LightGBM (~1 GB per
worker on a large assay; peak 6.6 GB over 13 workers in the full run, no swap); ridge on
`seq_struct` needs 80,000.

*Check:* ridge `seq_struct` with 13 jobs reproduces the committed results (same 3,189 chosen α;
Spearman within 1.2e-7, the order of single- vs multi-threaded BLAS sums) in **1,913 s vs
4,256 s**. Only 2.2×: the serial run already used several cores through Accelerate's BLAS
(18,187 CPU-seconds in the parallel run).

Finished assays are checkpointed (`<out_dir>/.cv_<label>_checkpoint/`, keyed by a fingerprint of
model, feature set, schemes and params) so a killed run resumes; `train.assays` restricts a run
to a subset. `train shap` and `train canary` use the same pool; the canary's (assay, scheme) cases
each draw from their own generator seeded with `(seed, assay index, scheme index)`, so its table
does not depend on `--jobs` either. (The committed ridge canaries were made with the earlier
shared generator.)

### 2. Torch and LightGBM never share a process

On macOS LightGBM's wheel links Homebrew's OpenMP runtime (`brew install libomp`) and torch
bundles another. With torch loaded first, LightGBM segfaulted on its first `Dataset.construct()`
(exit 139); loading LightGBM first instead made ESM-2's forward pass segfault in the test suite.
Two OpenMP runtimes in one process are not safe in either order, so: importing the CLI, the
registry or `models.lgbm` loads neither library (torch is imported inside `features embed`,
LightGBM inside `InnerCVLightGBM.fit`); a `train` process never loads torch and a `score`
process never loads LightGBM. Tests that fit LightGBM carry the `lightgbm` marker, are deselected
by default and run by `make test` in a second pytest process over files that never import torch;
a conftest guard fails a test that breaks the rule. `tests/test_openmp_isolation.py` checks the
imports in fresh interpreters. Linux wheels share `libgomp` and are unaffected.

### 3. The model: Optuna inside the training folds, like ridge's α

`models.lgbm.InnerCVLightGBM`, registered as `lgbm` and `lgbm_pca` (one class; the latter's
params set `pca_components: 32`):

- **10 Optuna trials per outer fold** (TPE, seeded; with Optuna's default 10 start-up trials this
  is a seeded random search). A trial is scored by the pooled squared error of an inner CV that
  holds out each training fold in turn, split like the outer scheme (ADR 0021). Tuning cannot be
  shared across the outer folds of an (assay, scheme): another fold's training rows include this
  fold's test rows. So there are 50 trials per (assay, scheme).
- **Rounds** from the inner curves: each inner booster stops 30 rounds after its held-out error
  last improved; the pooled curve is summed over inner folds up to the shortest one, and its
  argmin is the trial's number of rounds. Refit on all training rows with the best trial.
- Searched: learning rate [0.05, 0.2] (log), leaves [4, 64] (log, int), min data in leaf
  [5, 100] (log, int), feature fraction [0.1, 1], bagging fraction [0.5, 1], L2 [1e-3, 10] (log).
  Fixed: L2 objective, bagging every round, one thread, `deterministic`, row-wise histograms.
- **Guard:** as for ridge, a test changes every label of one test fold (permuted; ×7 − 3) and
  requires that fold's predictions to be bit-identical, under all three schemes, with and without
  PCA.

### 4. PCA of the embeddings is fitted on the rows each booster trains on

`models.pca.EmbeddingPCA` replaces each `<model>:embedding_*` block with its leading 32
components (eigendecomposition of the smaller Gram matrix; sign fixed so the largest loading is
positive; capped at the rank of the centred rows). It is fitted inside `fit`: anew for each inner
split and once more for the refit, so rows a booster is evaluated on never shape its components.
*Test:* replacing the embeddings of one test fold's rows with large noise leaves that fold's
fitted components and mean bit-identical, while every other fold's PCA (which trained on those
rows) changes.

### 5. SHAP: exact TreeSHAP, summarised per block

LightGBM's `pred_contrib` gives exact TreeSHAP values (no `shap` dependency); a test checks they
sum to the prediction minus a constant. `train shap` fits the model on all of an assay's variants
(its own search over the random folds) and writes feature- and block-level importance. A block's
importance is the mean over variants of |sum of its columns' SHAP values|: summing per-column
importances would rank a 1,280-column embedding by its width. Assays: the 5th percentile, median
and 98th percentile of size among the 213 structure assays (SCN5A_HUMAN_Glazer_2019, 224;
NUSA_ECOLI_Tsuboyama_2023_1WCL, 1,306; HSP82_YEAST_Flynn_2019, 13,294). `train figures` draws
them from the results file.

### 6. Budget changes, from the pilot

The first pilot (10 assays at size deciles, `lgbm` on `seq_struct`, learning rate ≥ 0.01, up to
1,000 rounds, patience 50) took 0.55–2.25 s per variant per worker: **15–35 h for one run, 35–85 h
for all four**, against a 12 h budget. Profiling one outer fold (single thread):

| Setting | RBP1, 975 variants | SPIKE_SARS2, 3,798 | Test ρ (SPIKE) |
|---|---:|---:|---:|
| as piloted (column-wise) | 149 s | 729 s | 0.901 |
| row-wise histograms | 112 s | 556 s | 0.901 (identical models) |
| + learning rate ≥ 0.05, ≤ 300 rounds, patience 30 | — | 193 s | 0.899 |
| + feature fraction ≤ 0.5 | 104 s (alone) | 152 s | 0.895 |
| PCA (32 per embedding) + row-wise | 8 s | 25 s | 0.902 |
| PCA + row-wise + learning-rate floor | — | 11 s | 0.900 |

Adopted: row-wise histograms (identical results) and the learning-rate floor with its round cap,
for both models (the 0.01 floor was this rung's own default; the pilot's winners took a median
177 rounds). Not adopted: the feature-fraction cap (costs accuracy), `max_bin` 63 (no gain). The
trial budget is unchanged. In the full runs the cap is not binding: 8–25 of ~3,200 fits per run
used all 300 rounds (median 67–80, median learning rate 0.072–0.077). The re-pilot ran ~3× faster
than the first (RBP1 1,992 → 535 s, OTC 4,308 → 1,580 s).

**`lgbm` on `seq` was not run** (decided after the pilot): at ~5 h it would add a second PCA
comparison only. `lgbm_pca` covers both feature sets; with vs without PCA is compared on
`seq_struct`.

### 7. A post-hoc diagnostic

`seq_struct_noemb` (`seq_struct` without either embedding), run with `lgbm`, was added **after**
the first results, to ask whether the embeddings make the trees overfit on unseen positions. It
is labelled post-hoc in the config and here and is not a candidate headline model. (With no
embedding there is nothing for PCA to reduce, so it runs as `lgbm`, the same search space.)

## Results (ProteinGym v1.3, pooled Spearman, ProteinGym aggregate, 95% bootstrap CI)

| Run | Assays | random | modulo | contiguous | Wall time (13 jobs) |
|---|---:|---:|---:|---:|---:|
| ridge `seq_struct` (ADR 0021) | 213 | 0.721 | 0.593 | 0.553 | 1,913 s |
| `lgbm_pca` `seq_struct` | 213 | 0.745 [0.730, 0.763] | 0.582 [0.559, 0.606] | 0.532 [0.504, 0.565] | 1,832 s |
| `lgbm` `seq_struct` | 213 | 0.734 [0.714, 0.760] | 0.568 [0.546, 0.591] | 0.530 [0.509, 0.553] | 32,054 s ¹ |
| `lgbm_pca` `seq` | 217 | 0.731 [0.714, 0.752] | 0.531 [0.506, 0.557] | 0.465 [0.436, 0.495] | 876 s |
| *post-hoc* `lgbm` `seq_struct_noemb` | 213 | 0.741 [0.723, 0.762] | 0.560 [0.535, 0.585] | 0.520 [0.493, 0.549] | 544 s |

¹ Plus ~2.5 worker-hours for 9 assays taken from the re-pilot's checkpoint (same settings).
Pooled MSE (training-SD units): `lgbm` `seq_struct` 0.418 / 0.716 / 0.768 against ridge's
0.458 / 0.720 / 0.776.

These CIs describe each run; they do not compare runs. **Paired** (`A − B`, 10,000 resamples;
"A > B" is the share of resamples):

| A − B | random | modulo | contiguous |
|---|---:|---:|---:|
| `lgbm_pca` − ridge `seq_struct` | +0.024 [+0.019, +0.028] | **−0.011** [−0.021, −0.003] | **−0.020** [−0.037, −0.005] |
| `lgbm` − ridge `seq_struct` | +0.013 [+0.006, +0.021] | **−0.025** [−0.034, −0.018] | **−0.023** [−0.034, −0.013] |
| `lgbm_pca` `seq` − ridge `seq` (217) | +0.020 [+0.016, +0.023] | −0.007 [−0.018, +0.006] | −0.010 [−0.029, +0.008] |
| `lgbm_pca` − zero-shot ESM-2 650M | +0.331 [+0.299, +0.361] | +0.168 [+0.137, +0.194] | +0.116 [+0.081, +0.147] |
| `lgbm` − zero-shot ESM-2 650M | +0.320 [+0.283, +0.357] | +0.155 [+0.123, +0.180] | +0.114 [+0.084, +0.137] |
| `lgbm_pca` − zero-shot SaProt 650M | +0.287 [+0.263, +0.309] | +0.124 [+0.099, +0.143] | +0.072 [+0.044, +0.096] |
| `lgbm` − zero-shot SaProt 650M | +0.276 [+0.247, +0.305] | +0.110 [+0.086, +0.129] | +0.069 [+0.048, +0.085] |
| `lgbm` − `lgbm_pca` (with − without PCA) | −0.011 [−0.019, −0.003] | −0.014 [−0.019, −0.009] | −0.002 [−0.016, +0.010] |
| *post-hoc* `noemb` − `lgbm` | +0.007 [+0.001, +0.012] | −0.008 [−0.016, −0.002] | −0.010 [−0.020, +0.000] |
| *post-hoc* `noemb` − ridge `seq_struct` | +0.019 [+0.016, +0.023] | −0.033 [−0.046, −0.023] | −0.033 [−0.047, −0.020] |

Per assay, `lgbm_pca` beats ridge `seq_struct` in 179 / 213 assays under random (median
+0.017), 91 under modulo (median −0.008) and 93 under contiguous (median −0.015).

### What the numbers mean

- **LightGBM beats ridge only on seen positions.** Under random it gains +0.013 to +0.024; under
  modulo and contiguous, where every test position is unseen, it loses 0.011 to 0.025 on
  `seq_struct`, every interval excluding 0. The rung's expectation (better under all three splits)
  does not hold. Trees split the feature space into boxes; a linear model on the same embeddings
  and log-probabilities extrapolates more smoothly to residues it never saw. On `seq` the losses
  are smaller and their intervals include 0.
- **The embeddings are not the cause.** The post-hoc run without them is *worse* than `lgbm` on
  unseen positions (−0.008 modulo, −0.010 contiguous, the latter's interval reaching 0) and only
  better under random (+0.007): the embeddings help extrapolation; they do not overfit it.
- **PCA helps a little and is 17× cheaper**: with − without PCA is −0.011 (random) and −0.014
  (modulo); contiguous is indistinguishable. 32 components per embedding keep what the trees use
  and remove 2,500 columns for `feature_fraction` to dilute.
- **Both beat zero-shot under every split**, by +0.07 to +0.33.
- Nothing here is "surprisingly good": the random-split gain over ridge is small, and the canary
  (below) passes.

## SHAP (`lgbm`, `seq_struct`, fit on all variants)

| Block | HSP82 (13,294) | NUSA (1,306) | SCN5A (224) |
|---|---:|---:|---:|
| SaProt 650M score | 26% | 22% | 33% |
| SaProt 650M log-probabilities | 8% | 29% | 0% |
| SaProt 650M embedding | 27% | 8% | 7% |
| ESM-2 650M embedding | 19% | 12% | 27% |
| ESM-2 650M score | 9% | 9% | 21% |
| wild-type / mutant one-hot | 8% | 15% | 0% |
| structure (pLDDT, SASA, contacts) | 0.2% | 0.5% | 0% |

- The zero-shot scores are near the top in all three (SaProt's score first or second).
- The three explicit structure features are almost unused. Structure reaches the model through
  SaProt instead (its tokens include Foldseek 3Di); the SaProt blocks are the largest share in
  every assay. "Structure near the top" holds only in that sense.
- **SCN5A is overfitting, not signal.** The ESM-2 embedding's 27% there comes from 17 of its
  1,280 columns. No supervised model learns SCN5A: out-of-fold Spearman under random is 0.013
  (`lgbm`), 0.030 (no embeddings), 0.068 (`lgbm_pca`) and 0.095 (ridge), against 0.221 for
  SaProt's zero-shot score alone. SHAP of a model fit on 224 noisy labels describes what it
  memorised; it is reported, but read as a warning, not as feature biology.

## Canary on the LightGBM models

`train canary`, 3 assays × 3 schemes, 20 permutations, pass at |z| ≤ 4:

| Run | Passed | Informative (true-label ρ ≥ 0.2) | Largest \|z\| |
|---|---:|---:|---:|
| `lgbm_pca` `seq_struct` | 9 / 9 | 9 (ρ 0.54–0.85) | 1.76 |
| `lgbm` `seq_struct` | 9 / 9 | 9 (ρ 0.57–0.84) | 2.16 |

No permuted mean is positive by more than 2.2 standard errors; a leak would push every case
up, as the oracle in ADR 0019 does (+0.50 to +0.80).

## Alternatives rejected

- **2 trials per outer fold** (10 per assay and scheme literally). 5× cheaper but barely a
  search; the cheaper boosters gave the same speed at no accuracy cost.
- **One study per (assay, scheme) scored across outer folds.** Leaks: the other folds' training
  rows contain this fold's test rows.
- **`joblib`** for the pool: untyped under pyright strict; the stdlib pool needs only picklable
  work.
- **Loading LightGBM before torch** in `mutascope/__init__`: moved the crash from LightGBM to
  torch's forward pass.
- **The `shap` package**: LightGBM's own TreeSHAP is exact and needs no dependency.

## Consequences

- New dependencies: `lightgbm` 4.7.0, `optuna` 5.0.0. On macOS LightGBM needs `brew install
  libomp`.
- `make test` runs two pytest processes; plain `pytest` skips LightGBM tests.
- The MLP rung inherits `--jobs`, the checkpoint and the memory budget; it uses torch, so it can
  never run in a process that has loaded LightGBM.
- `train cv`, `train shap`, `train canary` and `train figures` are CLI commands, not yet Nextflow
  processes.
