# 0019 — Supervised cross-validation harness: folds, metrics and a leakage canary

- Status: accepted
- Date: 2026-10-14
- Builds on: ADR 0005 (scores and labels come from the processed table), ADR 0010 / 0011
  (metric parity with ProteinGym's own code)

## Context

Phase 3 trains per-assay models under ProteinGym's supervised protocol. Before any real model,
the harness has to be right: which variants are in which fold, what each fold's model is
trained on, and how the per-assay number is computed. ProteinGym publishes three CV schemes
per assay (`fold_random_5`, `fold_modulo_5`, `fold_contiguous_5`) in
`cv_folds_singles_substitutions/<DMS_id>.csv`, but those files print scores with one digit
fewer than the DMS files and have empty labels in 6 assays (ADR 0005), so they may only supply
folds.

How ProteinGym computes its supervised numbers is not in one file. `performance_DMS_supervised_
benchmarks.py` takes one Spearman and one MSE per (model, assay, scheme) as input; those come
from `merge_supervised.py`, which reads each model's out-of-fold predictions; and the
predictions and their targets come from ProteinNPT (`scripts/train.py`,
`proteinnpt/utils/data_utils.py`), the code that produced ProteinGym's supervised scores. All
three were read at pinned commits: ProteinGym `1f8de97` (the release pin), ProteinNPT
`ba50f15`.

## Decision

### 1. Folds: joined on `(assay_id, mutant)`, keys only, checked

`mutascope data folds` reads `mutant` and the three fold columns of each fold file (as text,
cast to integers) and writes `processed/folds/assay_id=<id>/data.parquet` with
`assay_id, mutant, position, fold_random, fold_modulo, fold_contiguous` and nothing else. The
join fails, listing every bad assay and writing nothing, if a variant has no fold or a null
fold, a mutant is repeated, or the fold file holds a mutant the processed table lacks.
`FoldAssignmentSchema` adds two checks of the position-based schemes: modulo and contiguous
folds hold whole positions, and contiguous folds never decrease along the sequence (a swap of
the two columns fails). `load_cv_table` joins labels from `variants` to `folds` again for
training and fails on any unmatched row, so a stale fold table cannot drop variants.

On v1.3: 217 assays, 696,311 variants, every one matched, no repeats, no nulls. Two facts the
code relies on, found while checking the files:

- **The modulo fold is the rank of the position among the assay's mutated positions, mod 5**,
  not the residue number mod 5 (that matches 53% of variants). True for all 217 assays.
- **Two assays have 4 folds in every scheme**: `F7YBW8_MESOW_Aakre_2015` (37 variants) and
  `SPG1_STRSG_Wu_2016` (76) mutate only 4 positions. The loop uses the folds present; it never
  assumes 0–4.

### 2. The loop: train on the other folds, standardise with training statistics

Per assay and scheme, each fold in turn is the test set and a fresh model is trained on all
the others. ProteinNPT's default (`use_validation_set` off) holds out no validation fold, so we
train on 4 folds. As in ProteinNPT's `preprocess_training_targets` / `preprocess_test_targets`,
the target is `DMS_score` standardised with the **training folds'** mean and population
standard deviation (`np.nanstd`, ddof 0), and the test fold's targets get the same transform.
A unit test checks both arrays against a copy of that ProteinNPT code. Test data never shapes
the scale.

### 3. Metrics: Spearman and MSE over the pooled out-of-fold predictions

`merge_supervised.py` computes, per assay and scheme, `normalized_targets.corr(predictions,
method="spearman")` and the mean squared difference over **all rows**, i.e. over the 5 folds'
out-of-fold predictions pooled. It does not average per-fold values. (ProteinNPT's
`train.py` logs both a pooled and a mean-over-folds Spearman, but the pooled one is what
ProteinGym's leaderboard uses.) We do the same: the `fold`-null row of each (assay, scheme) is
the pooled value and the one to aggregate; rows with `fold = k` are the metric on fold k alone,
a diagnostic only. A unit test checks the pooled values against `merge_supervised.py`'s
formula to 1e-12.

The choice matters per assay. With a small probe model (ridge on one-hot residues and position)
over all 217 assays, pooled and mean-of-folds Spearman differ by more than 0.05 in 45 assays
under contiguous, 15 under modulo and 4 under random, by up to 0.49. Averaged over assays the
two nearly agree (mean difference −0.015 contiguous, −0.004 modulo, +0.001 random), which is
how the difference could go unnoticed.

Two consequences of pooling on a per-fold scale, both inherited from ProteinGym and accepted:
the pooled targets are each fold's own standardisation, so the pooled ranking is not exactly
the raw `DMS_score` ranking; and MSE is in training-standard-deviation units, so it compares
across assays.

AUC, MCC, NDCG and top-10% recall (the zero-shot metrics) are not computed yet. ProteinGym
reports none of them for supervised models, and on a constant predictor (the baseline below)
NDCG depends on how ties are broken. They are added when the first real model lands.

### 4. Results: long format, one file per model and feature set

`mutascope train cv` writes `results/supervised/cv_<model>_<feature_set>.parquet` validated by
`SupervisedMetricSchema`: `model, feature_set, assay_id, scheme, fold, metric, value`. It
prints the ProteinGym-style aggregate of the pooled values per scheme (no CI yet) and logs one
MLflow run to the `supervised` experiment (`train.experiment`). The logged metrics are counts
taken from the results file. Model and feature set are names in registries
(`models.registry.MODELS`, `features.base.FEATURE_SETS`), chosen in `conf/train/default.yaml`.

**Feature builders see keys only** (`assay_id, mutant, position, wt_aa, mt_aa`), never
`DMS_score` or `DMS_score_bin`. That is the guard against target leakage through features,
which the canary below cannot catch.

### 5. Baseline: predict the training mean

`training_mean` predicts the mean of the standardised training targets, i.e. 0. Its Spearman
is undefined (NaN: a constant) and its MSE is the test targets' mean square, the bar every
model has to clear. On v1.3 (ProteinGym aggregate of pooled MSE over 217 assays): random
1.0310, modulo 1.1182, contiguous 1.1450. MSE above 1 grows as the test fold looks less like
the training folds.

The mean is rounded to 12 decimals. Unrounded, its floating-point residue (median 5e-17, at
most 4.1e-15; nonzero in 3,070 of 3,249 training sets) differs from fold to fold. The pooled
predictions were then not one constant, and the pooled Spearman ranked the noise: −0.67 to
+0.91, "defined" in all 217 assays.

### 6. Canary: label permutation, judged on the mean over permutations

`models.canary.permutation_canary` runs the loop once with true labels and `n` times with the
training targets shuffled before each fit (test targets untouched). It passes when the mean
permuted pooled Spearman lies within `k` standard errors of 0 (`sd / √n`).
`tests/models/test_canary.py` runs it with `n = 20`, `k = 4` on synthetic assays (always) and
on three small real assays when the processed data is present: `ERBB2_HUMAN_Elazar_2016`
(Expression, 326 variants), `OTU7A_HUMAN_Tsuboyama_2023_2L2D` (Stability, 635) and
`NPC1_HUMAN_Erwood_2022_HEK293T` (Activity, 637), under all three schemes. The learner is the
test probe, because the baseline has no Spearman to lose. A positive control requires the probe's
true-label Spearman to be ≥ 0.2 in every case (observed 0.265–0.763), since a near-0 permuted
value proves nothing about a model that learns nothing.

**Why the mean and not one run.** One shuffled run reaches |ρ| = 0.165 on these assays (up to
0.22 on others of similar size), well above 1/√n, because the predictions are a random
projection of features that do carry signal. The mean over 20 permutations is within 1.72
standard errors of 0 in all 9 cases (largest |mean| 0.021).

**What it catches, measured on the same 9 cases.** A loop whose fit includes the test rows
(with only the training folds' labels shuffled): mean permuted ρ +0.50 to +0.80, and no
single run below +0.40. A model with a back door to the labels fails on synthetic data in CI
(mean > 0.99).

**What it cannot catch.** A feature computed from labels with the test folds included (here the
mean `DMS_score` at the variant's position, a target encoding) passes all 9 cases (|z| ≤
1.70). Fit on shuffled labels, the model gives that feature a weight of random sign, so the
mean stays near 0. Shuffling the whole assay's labels before features are built does flag it
on modulo/contiguous folds in two of three assays tried in a scratch check (+0.16 to +0.34;
about +0.02 in the third), and only if the leaking feature is rebuilt from the shuffled labels. The structural rule in decision 4 is the guard
instead: feature builders never receive labels, and any label-based feature must be fit inside
the training folds like a model.

## Consequences

- `results/supervised/` holds the supervised results; CLAUDE.md's results line now names the
  supervised columns (`feature_set`, `scheme`).
- The nested hyperparameter search (Optuna) will run inside `cross_validate`'s training set,
  so the test fold still never shapes a model, its scale or its hyperparameters.
- The fold step is a CLI command but not yet a Nextflow process: wiring it needs the fold files
  in the staged input layout (`data.staging`) and the `test` subset. That comes with the first
  real model.
- Bootstrap CIs and paired comparisons of supervised results (with a summary schema that
  allows MSE) are not built yet; they are needed for the Phase 3 Spearman table.
