# 0024 — Prediction intervals: CV+ inside the training folds, two calibration modes, two widths

- Status: accepted
- Date: 2026-10-16
- Builds on: ADR 0019 (CV loop), ADR 0021 (ridge, inner CV split like the outer scheme), ADR 0022
  (`--jobs`, checkpoint, memory budget; deployment chosen on unseen positions), ADR 0023 (MLP)

## Context

The deployed model (ridge `seq_struct`, ADR 0022/0023) gives a point prediction per variant. The
website will show every one of the 19 × L substitutions, almost all unmeasured, so each needs an
honest interval: a range that holds the true score as often as it claims. Task 7 fixed the design
before any result:

- a **fold-ensemble spread** per variant, as a difficulty score;
- **conformal intervals** at 80% and 90% by **CV+** (cross-conformal), computed inside the
  training folds only;
- two **calibration modes**: (a) random calibration folds; (b) folds that mimic the outer scheme;
- two **interval types**: (a) constant width; (b) normalised (adaptive), residuals divided by the
  fold-ensemble spread plus a small `epsilon`;
- judged on the test folds per scheme: coverage against target (mean over assays, distribution,
  assays more than 10 points below), width relative to each assay's score SD, whether adaptive
  width goes with error, coverage by assay size.

## Decision

### 1. CV+ is implemented here, not taken from MAPIE

MAPIE 1.5.0 (latest, 2026-08-05) has `CrossConformalRegressor(method="plus")`, but its source shows
three blockers: `ResidualNormalisedScore` is documented "only with split and prefit methods (not
with cross methods)", so the adaptive interval is unavailable; `fit_params` are passed whole to
every fold's estimator, so our models cannot receive their per-row folds for their own inner CV
(ridge's penalty, the MLP's early stopping); and it needs array inputs, not our `FeatureMatrix`
with its site one-hot. `models.conformal.CVPlus` is ~150 lines and has no new dependency.

**CV+** (Barber, Candès, Ramdas & Tibshirani, 2021, *Ann. Statist.* 49:486): split the calibration
rows into K folds; fit `M_k` without fold k; each row i of fold k gets `R_i = |y_i − M_k(x_i)|`. At a
new x, lower = the ⌊α(n+1)⌋-th smallest of `M_k(i)(x) − R_i`, upper = the ⌈(1−α)(n+1)⌉-th smallest
of `M_k(i)(x) + R_i`. Every row is scored by the one model that never saw it, and every row is
used for both fitting and calibrating.

### 2. Where the pieces come from, per outer fold

For each assay, scheme and outer (test) fold, from the training rows only:

1. **The outer model** (as `train cv`): the point prediction the interval goes with. Its pooled
   Spearman reproduces `cv_ridge_seq_struct.parquet` (checked on every assay, below).
2. **CV+, once per mode.** `scheme`: the calibration folds are the training rows' own outer folds,
   so under modulo / contiguous each `M_k` is scored on positions it never saw, as the test fold
   will be. `random`: the training rows split into as many equal random folds, seeded by
   `(seed, crc32(assay), crc32(scheme), outer fold)`. Each `M_k` gets the same kind of folds for
   its own inner CV, so a mode is one way of splitting, all the way down.
3. Intervals at the test rows, for both types and both levels.

**Guard:** changing every label of one test fold (permuted; ×7 − 3) leaves that fold's
predictions, spreads and every bound bit-identical, under all three schemes; the other folds'
intervals do change (the test would otherwise prove nothing).

### 3. The normalised score divides by `M_k`'s own fold-ensemble spread

The **fold-ensemble spread** of a fitted model is the sample SD of its fold ensemble's
predictions: one member per training fold, fit without it. For the deployed all-data model that is
exactly "the SD of the 5 fold models' predictions".

The brief's normaliser ("residuals divided by the fold-ensemble spread") needs one refinement. The
spread of the K CV+ models cannot score a calibration row: K − 1 of them trained on it, so it would
be systematically smaller there than at a new point, and the normalised scores would not be
exchangeable with a test point's. So each calibration row is divided by the spread of the fold
ensemble of **the model that scored it**, `M_k`, whose members all exclude fold k. That is a
function fitted without fold k, like `M_k` itself, so the CV+ argument is unchanged:

- calibration: `R_i = |y_i − M_k(x_i)| / (s_k(x_i) + ε)`;
- interval at x: CV+ order statistics of `M_k(i)(x) ± R_i (s_k(x) + ε)`.

The **difficulty score** reported per variant is the spread of the K CV+ models themselves at the
test point (all K exclude it): K = 4 inside an outer fold, 5 for the deployed model.

*Ridge gives its ensemble for free:* `InnerCVRidge` already fits every "all but one training fold"
model to choose α; it now keeps those fits at the chosen α (`fold_predict`). Its own numbers are
unchanged. A model without that hook (the MLP) gets one member fitted per training fold.

`ε = 0.01` training-target SDs (the loop standardises `DMS_score`): only a guard against a zero
spread, below any difference a DMS score resolves; set before any result.

### 4. Tests (`tests/models/test_conformal.py`, `test_uncertainty.py`, `test_cli_uncertainty.py`)

- Order statistics equal a brute-force sort, chunked and unchunked.
- **Known noise level** (y = x b + N(0, 1), 20 draws × 400 rows, ridge, 4 folds): absolute CV+
  coverage 0.809 at 80% and 0.905 at 90% (test: within 0.02), mean width 2.62 and 3.37 against the
  oracle's 2.56 and 3.29.
- **Normalised scores are exchangeable:** used as split conformal (one fold) they cover 0.806 /
  0.905. CV+ over them covers 0.836 / 0.939 on the same data: when the spread carries no
  information (homoscedastic noise), each test point's scale differs between the K models (by 29%
  on average here), which widens CV+'s order statistics. CV+ guarantees a lower bound, not an exact
  level; the test requires no under-coverage beyond 0.02.
- Each calibration row's prediction and spread equal those of a model fit without its fold; the
  ridge fold ensemble equals ridge refits at the chosen α (1e-12); a model without the hook gets
  one member per fold.
- No test-fold label reaches an interval (section 2); the all-data calibration is reproduced
  exactly by refitting.

### 5. Saved for Task 8: the final models' calibration

`mutascope train calibrate` (3,880 s on 13 workers; 3,994,758 rows = 6 × 665,793 variants, 75 MB)
fits CV+ on **all** of an assay's variants (targets standardised with
all variants' mean and SD, as the final model will be), for every scheme's folds and both modes,
and writes `data/processed/calibration/<label>/<assay>.parquet` (`CalibrationSchema`, gitignored):
per variant, the calibration fold, `M_k(x_i)`, `s_k(x_i)`, the standardised target, and the
assay's `target_mean` / `target_sd`.

**How Task 8 produces an interval for any variant x (measured or not):**

1. Choose the scheme and mode. The results argue for `scheme` mode with the modulo or contiguous
   folds (the landscape is unseen positions) and constant width; section "Consequences".
2. Refit `CVPlus.fit(x_all, z_all, folds, make_model)` on the same features, targets and folds.
   Fits are deterministic: its `calibration.prediction` and `.spread` must equal the saved columns
   (exactly with one BLAS thread, as `train calibrate`'s workers run; to ~1e-7 with several, the
   order of BLAS sums). Checked on NUSA_ECOLI_Tsuboyama_2023_1WCL (contiguous, scheme mode): maximum
   difference 0.0. A larger mismatch means the inputs changed.
3. `at = cvplus.predict(x)` gives the 5 fold models' predictions and spreads at x;
   `cvplus.intervals(at, interval, levels, epsilon)` gives `(lower, upper)` in standardised units;
   map back with `target_mean + target_sd × bound`. The difficulty score is
   `at.ensemble_spread()`.
4. The point prediction is the final all-data model's, as in this evaluation.

Saving the residuals rather than pickled models keeps the store small, auditable and independent
of Python object layout; ridge refits in seconds per assay.

## Results

ProteinGym v1.3, ridge `seq_struct`, 213 assays, 665,793 variants, every variant's interval
from its own outer fold. Files: `results/supervised/uncertainty_ridge_seq_struct{,_summary,_bins}
.parquet`; figures `results/figures/{calibration,width_error}_ridge_seq_struct.{svg,png}`. Wall time
10,926 s on 13 workers (80,000-variant memory budget). The outer model's pooled Spearman equals
`train cv`'s in all 639 (assay, scheme) cells (max difference 1.2e-7, BLAS summation order).

### Coverage on the test folds (mean over assays of per-assay coverage; "below" = assays more than 10 points under target; ProteinGym aggregate within 0.006 of the mean everywhere)

| Split | Calibration | Interval | 80%: mean [q10, q90] | below | 90%: mean [q10, q90] | below |
|---|---|---|---|---:|---|---:|
| random | random | constant | 0.818 [0.807, 0.833] | 0 | 0.911 [0.903, 0.920] | 0 |
| random | random | adaptive | 0.850 [0.837, 0.868] | 0 | 0.945 [0.934, 0.958] | 0 |
| random | scheme | constant | 0.819 [0.808, 0.837] | 0 | 0.911 [0.905, 0.920] | 0 |
| random | scheme | adaptive | 0.850 [0.839, 0.865] | 0 | 0.943 [0.935, 0.955] | 0 |
| modulo | random | constant | **0.699** [0.611, 0.802] | **95** | **0.828** [0.754, 0.901] | **59** |
| modulo | random | adaptive | **0.637** [0.541, 0.750] | **164** | **0.803** [0.716, 0.893] | **103** |
| modulo | scheme | constant | 0.816 [0.807, 0.826] | 1 | 0.909 [0.903, 0.917] | 1 |
| modulo | scheme | adaptive | 0.846 [0.833, 0.858] | 0 | 0.942 [0.932, 0.952] | 1 |
| contiguous | random | constant | **0.673** [0.567, 0.782] | **138** | **0.806** [0.719, 0.891] | **86** |
| contiguous | random | adaptive | **0.598** [0.502, 0.717] | **186** | **0.767** [0.677, 0.869] | **137** |
| contiguous | scheme | constant | 0.815 [0.804, 0.827] | 0 | 0.908 [0.899, 0.916] | 0 |
| contiguous | scheme | adaptive | 0.845 [0.831, 0.860] | 0 | 0.940 [0.927, 0.953] | 0 |

**By assay size** (scheme calibration, constant width, 90%): 0.904–0.923 in every bin (< 300,
300–999, 1,000–1,999, 2,000–4,999, ≥ 5,000 variants) under every split. The one assay below target
is F7YBW8_MESOW_Ding_2023 under modulo (80 variants, 0.675). With random calibration under
contiguous, 25 of 42 assays of 300–999 variants and 33 of 74 of 1,000–1,999 are below target.

### Width (mean over assays, assay-`DMS_score`-SD units; median in brackets)

| Split | random calib., constant 90% | scheme calib., constant 90% | scheme calib., adaptive 90% |
|---|---:|---:|---:|
| random | 2.04 [2.05] | 2.05 [2.04] | 2.93 [2.83] |
| modulo | 2.00 [1.98] | 2.90 [2.46] | 3.94 [3.46] |
| contiguous | 1.99 [1.97] | 3.07 [2.61] | 4.15 [3.66] |

### Does width (or spread) go with error? (per-assay Spearman with |outer prediction − target|; median, share of assays > 0)

| Split | spread of the K fold models (scheme calib.) | adaptive 90% width (scheme) | constant 90% width (scheme) |
|---|---|---|---|
| random | 0.171 (98%) | 0.185 (99%) | 0.093 (94%) |
| modulo | 0.087 (92%) | 0.095 (94%) | 0.025 (67%) |
| contiguous | 0.084 (85%) | 0.097 (83%) | 0.010 (55%) |

Across width deciles (figure), adaptive widths under contiguous run from 1.6 to 7.8 assay SDs
while mean |error| rises from 0.53 to 0.72.

### What the numbers mean

- **Calibration must mimic the test split.** With scheme-like calibration folds every split hits
  its target (0.908–0.911 at 90%, 0.815–0.819 at 80%; ≤ 1 assay of 213 more than 10 points below).
  With random calibration folds the intervals are as narrow under modulo / contiguous as under
  random (~2.0 SDs), because the calibration rows sit at positions the calibration models trained
  on; on unseen positions they under-cover (0.806 at 90% under contiguous; 86 assays below). It is
  the conformal counterpart of ADR 0021's rule that the inner CV is split like the outer one.
  Extreme case: CCDB_ECOLI_Adkar_2012 reports one score per position (all 100 positions have a
  single value), so a model that has seen a position fits it exactly; random calibration gives a
  0.009-SD interval and 0.000 coverage under modulo, scheme calibration 2.64 SDs and 0.906.
- **Honest intervals on unseen positions are wide:** ~2.5–3 assay SDs at 90% (median 2.46 modulo,
  2.61 contiguous) against ~2.0 on seen positions. That is the price of predicting the full
  landscape, and it matches the error: the outer model's MSE there is 0.72–0.78 training-SD² (ADR
  0021).
- **Adaptive intervals are conservative and wider, and track error only weakly.** They over-cover
  (0.845–0.850 at 80%, 0.940–0.945 at 90%) at 35–43% more width, as the synthetic test predicted
  (section 4: CV+ over normalised scores is conservative when the K models' spreads disagree at a
  point). Their width does rank error (median Spearman 0.10 on unseen positions, 0.19 on seen
  ones), better than constant width (0.01–0.09), but weakly. The fold-ensemble spread alone carries
  the same signal as a difficulty score (0.08–0.17).
- Nothing here looked "too good": the one exact-zero coverage was traced to the data (above), and
  coverage on target is what CV+ promises, not a model score.

### MLP (`mlp_mse`) not run

Measured on NUSA_ECOLI_Tsuboyama_2023_1WCL (1,306 variants, contiguous, one process): the full
design costs 16.2× a plain CV fit (the MLP has no free fold ensemble, so each normalised calibration
model fits one member per fold); the full `train cv` run took 558 s (ADR 0023), so ~2.5 h, over the
1-hour limit set for the comparison. A reduced design (scheme calibration, constant width only)
measured 5.8× (~55 min); it is a different design and was not run.

## Alternatives rejected

- **MAPIE** (section 1).
- **Split conformal** (hold out one fold to calibrate): 20–25% fewer training rows for every model,
  and the intervals of one split depend on which fold was held out.
- **Normalising by the spread of the K CV+ models at calibration rows:** K − 1 of them trained on
  the row (section 3).
- **A learned residual model** (MAPIE's `ResidualNormalisedScore`): a second model per fold, with
  its own rows to hold out; the brief fixed the spread as the difficulty score.
- **Calibrating on the outer model's own inner-CV fits** (free for ridge): their α was chosen with
  the calibration fold's labels, so the residuals would be slightly optimistic.

## Consequences

- **For Task 8:** intervals on the website should be CV+ with scheme-like calibration folds
  (modulo or contiguous), constant width: on target on unseen positions with no assay more than 10
  points below (modulo: 1). Adaptive intervals are a defensible alternative only if their 4–5-point
  over-coverage and extra width are accepted; the fold-ensemble spread can be shown separately as a
  difficulty score. Never random calibration folds.
- New: `models.conformal`, `models.uncertainty`, `eval.uncertainty`; commands `train uncertainty`,
  `train calibrate`, `train uncertainty-figures`; config `train.uncertainty`. No new dependency.
- `InnerCVRidge` keeps its inner fits at the chosen α (`fold_predict`); its predictions are
  unchanged.
- The commands are not Nextflow processes yet, like the other `train` commands.
