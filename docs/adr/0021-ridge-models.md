# 0021 — Ridge models: one-hot, augmented (Hsu et al.) and all features, with inner-CV penalty

- Status: accepted
- Date: 2026-10-14
- Builds on: ADR 0019 (CV loop, pooled metrics, canary), ADR 0020 (feature store), ADR 0014
  (paired bootstrap)

## Context

ADR 0020 left `train cv` running the `none` feature set: `cv_metrics_table` took one matrix for
all assays, which for `seq_struct` would be 14.7 GB in float64. The first real models are three
ridge regressions (the bottom rung of the Phase 3 ladder):

- (a) **one-hot ridge**: the one-hot sequence alone;
- (b) **augmented ridge** (Hsu et al., 2022, *Nat. Biotechnol.* 40:1114): one-hot sequence plus
  a zero-shot score;
- (c) **ridge on all features**: the feature-store sets `seq` (217 assays) and `seq_struct` (213).

Each needs its regularisation strength chosen without touching the test fold, a zero-shot
comparator scored the same way, a paired comparison, and a sanity check against ProteinGym's
supervised leaderboard.

## Decision

### 1. Features are assembled per assay, from keys only

`cv_metrics_table(data, design, make_model, ...)` takes a `DesignBuilder`
(`(assay_id, keys) -> FeatureMatrix`) instead of a matrix. For each assay it passes that
assay's `KEY_COLUMNS` (never labels), runs every scheme, and drops the matrix before the next
assay. `train cv --store-dir` resolves `train.feature_set` to a key-only set (`none`) or a store
set (`assemble(...).feature_matrix()`, float64); a structure set runs only on the assays whose
store partition has structure features (213). A test checks the builder is called once per
assay with keys only.

### 2. The site one-hot is two numbers per row, never a matrix

Hsu et al. one-hot encode the whole sequence: 20 × L columns, up to 68,460 (ZIKV, 3,423
residues). For a single mutant only two entries differ from the wild type's encoding: residue
i's wild-type column goes 1 → 0 and its mutant column 0 → 1. A constant row shift is absorbed by
the intercept, so the row is stored as `+1` at `(i, mt)` and `−1` at `(i, wt)`
(`features.matrix.SiteOneHot`), and models use only `X v` and `X' u` (index and `bincount`).
It is a new block, `site_onehot`, assembled on demand and never written to the store. Sets:
`site_onehot`, `site_onehot_esm2` (`+ esm2_650m:score`); `esm2_score` and `saprot_score` hold one
score each for the zero-shot comparator.

### 3. One ridge class; the augmented score is exactly unpenalised

`models.ridge.InnerCVRidge` minimises `|y − b − Z g − X w|² + α|w|²`. `b` (intercept) and the
columns named in `train.params.<model>.unpenalized` (`Z`) are free; everything else (`X`) is
shrunk. "Free" is exact, not a small penalty: by Frisch–Waugh–Lovell, project `[1, Z]` out of
`X` and `y`, solve an ordinary ridge for `w`, and regress the remainder of `y` on `[1, Z]`. Hsu
et al. instead give the density feature a tiny penalty; this is its limit.

- `ridge` (a, c): nothing unpenalised. `augmented_ridge` (b): `esm2_650m:score` unpenalised, so
  the one-hot terms only model what the score gets wrong.
- Dense penalised columns are standardised with the fit rows' mean and population SD (a constant
  column stays 0), so one α treats an embedding dimension and BLOSUM62 alike. The site one-hot
  is not scaled (0/±1 already; Hsu et al. do not scale it).
- Solvers: dense, one `eigh` per fit of `X'X` (rows ≥ columns) or `XX'` (else) gives every α;
  with a site one-hot, Jacobi-preconditioned conjugate gradients on the normal equations, from
  the largest α down with warm starts (relative residual 1e-9).
- Tests check both paths against the closed-form solution of the full design matrix (dense:
  1e-8; site: 1e-6), the dense path against scikit-learn's `Ridge` on standardised columns
  (1e-10), and that at α = 1e9 the augmented model reduces to the least-squares slope on the
  score.

### 4. α is chosen by inner CV over the training folds, split like the outer scheme

The `Regressor` protocol is now `fit(x, y, groups)`: the loop passes each training row's outer
fold. `InnerCVRidge` holds each of those folds out in turn (4 inner folds; 3 in the two 4-fold
assays), fits the whole α path on the rest, and picks the α with the smallest squared error over
all held-out rows (ties → larger α), then refits on the whole training set. Under `contiguous`
the inner folds are contiguous blocks, so α is tuned for extrapolating to unseen regions, the
same task the test fold poses. A random inner split would tune for interpolation.

Grid: `10^-3 … 10^6`, 19 values (`train.params.ridge.alphas`). Every chosen α is written to
`cv_<label>_hyperparameters.parquet` (`SelectedHyperparameterSchema`), and `train cv` prints how
often it hit an end of the grid.

**Guard:** a test changes every label of one test fold (permuted, ×7, −3) and requires that
fold's predictions to be bit-identical, for the dense and the augmented path under all three
schemes. The penalty, the scaling and the coefficients all come from the other folds.

### 5. Zero-shot is scored by the same loop

`zero_shot` (`models.baselines.ZeroShot`) takes a one-score feature set and predicts the score
standardised with the training rows' mean and SD. It never reads labels, so its sign and
within-fold ranking are the zero-shot model's own; its pooled Spearman is the zero-shot ranking
judged exactly as a supervised model's (same test folds, same pooling of per-fold scales). That,
not the Phase 2 number, is what each supervised model is paired with.

### 6. Comparisons, the leaderboard check and the canary are commands

- `train compare` → `results/supervised/supervised_summary.parquet` (aggregate + bootstrap CI per
  run and scheme, `SummarySchema`) and `supervised_comparisons.parquet` (paired bootstrap of
  `A − B`, `ComparisonSchema`), for the pairs in `train.comparisons`; 10,000 resamples, seed 0.
  `ridge_seq_struct` is compared on the 213 assays it shares with the zero-shot run.
- `train leaderboard` → `leaderboard.parquet`: our aggregate per scheme next to ProteinGym's
  published supervised baselines of the same type, plus the Spearman between our per-assay values
  and theirs. Not a gate (section "Leaderboard").
- `train canary` → `canary_<label>.parquet` (`CanarySchema`): the ADR 0019 canary on the three
  canary assays × three schemes, now with the real models. A case is *informative* only if the
  true-label Spearman is ≥ 0.2; the command fails if any case's permuted mean is more than 4
  standard errors from 0.
- The supervised leaderboard files are pinned like the zero-shot ones (same commit,
  `data.supervised_benchmarks_url`, sha256 in the manifest). They are saved with a
  `Supervised_` prefix because upstream's summary has the zero-shot summary's file name.

## Results (ProteinGym v1.3, pooled Spearman, ProteinGym aggregate, 95% bootstrap CI)

| Run | Assays | random | modulo | contiguous |
|---|---:|---:|---:|---:|
| zero-shot ESM-2 650M (`zero_shot_esm2_score`) | 217 | 0.412 [0.389, 0.441] | 0.412 [0.388, 0.442] | 0.414 [0.390, 0.444] |
| zero-shot SaProt 650M (`zero_shot_saprot_score`) | 213 | 0.458 [0.441, 0.481] | 0.458 [0.440, 0.483] | 0.461 [0.441, 0.485] |
| (a) one-hot ridge (`ridge_site_onehot`) | 217 | 0.608 [0.588, 0.635] | 0.006 [−0.014, 0.022] | −0.014 [−0.043, 0.011] |
| (b) augmented ridge (`augmented_ridge_site_onehot_esm2`) | 217 | 0.676 [0.660, 0.698] | 0.408 [0.384, 0.438] | 0.409 [0.383, 0.439] |
| (c) ridge, `seq` | 217 | 0.711 [0.695, 0.732] | 0.538 [0.511, 0.564] | 0.475 [0.444, 0.507] |
| (c) ridge, `seq_struct` | 213 | 0.721 [0.705, 0.741] | 0.593 [0.571, 0.617] | 0.553 [0.528, 0.581] |

These CIs say how each run does on assays like these; they do not compare two runs. Pooled
MSE (training-SD units): one-hot 0.607 / 1.118 / 1.146, augmented 0.520 / 0.902 / 0.925, `seq`
0.470 / 0.778 / 0.852, `seq_struct` 0.458 / 0.720 / 0.776, against the training-mean baseline's 1.031 / 1.118 / 1.145
(ADR 0019). Zero-shot MSE is not meaningful (a z-scored score is not a calibrated prediction).

**Paired against zero-shot ESM-2 650M on the same test folds** (`A − B`, 10,000 resamples):

| A | random | modulo | contiguous |
|---|---:|---:|---:|
| one-hot ridge | +0.196 [+0.158, +0.234] | −0.406 [−0.442, −0.379] | −0.429 [−0.473, −0.395] |
| augmented ridge | +0.264 [+0.234, +0.294] | −0.003 [−0.006, −0.001] | −0.006 [−0.009, −0.003] |
| ridge `seq` | +0.299 [+0.267, +0.329] | +0.126 [+0.093, +0.151] | +0.060 [+0.027, +0.090] |
| ridge `seq_struct` (213 shared assays) | +0.307 [+0.276, +0.338] | +0.180 [+0.149, +0.206] | +0.137 [+0.104, +0.165] |

**Other pairs** (`train.comparisons`):

| A − B | Assays | random | modulo | contiguous |
|---|---:|---:|---:|---:|
| ridge `seq_struct` − zero-shot SaProt 650M | 213 | +0.263 [+0.239, +0.286] | +0.135 [+0.109, +0.156] | +0.092 [+0.065, +0.115] |
| ridge `seq` − augmented ridge | 217 | +0.035 [+0.029, +0.039] | +0.130 [+0.098, +0.153] | +0.066 [+0.034, +0.095] |
| ridge `seq_struct` − ridge `seq` | 213 | +0.009 [+0.007, +0.010] | +0.054 [+0.042, +0.068] | +0.076 [+0.061, +0.092] |

Every CI above excludes 0.

### What the numbers mean

- **One-hot ridge cannot rank unseen positions at all.** For a variant at a position no
  training row has, both of its one-hot entries have coefficient exactly 0, so the prediction
  is the fold's intercept. Under modulo and contiguous, every test position is unseen: all
  1,083 of 1,083 test folds are one constant, each per-fold Spearman is undefined, and the
  pooled value only ranks five fold-level constants. Its MSE there equals the training-mean
  baseline's (1.118 vs 1.118, 1.146 vs 1.145). Read 0.006 / −0.014 (and ProteinGym's
  0.022 / 0.059) as "zero", nothing finer.
- **On unseen positions, augmented ridge is zero-shot with a learned sign.** By the same
  argument its prediction there is the intercept plus γ × score. Within a test fold its
  Spearman equals zero-shot's exactly in 1,057 (contiguous) and 1,054 (modulo) of 1,083 folds
  and is exactly its negative in the other 26 and 30, where the training folds gave the score a
  negative weight. The small paired deficit (−0.003, −0.006) is that, plus pooling folds on
  different scales. Under random it combines the two (one-hot 0.621, zero-shot 0.427,
  augmented 0.682 as plain means; it beats the better of the two in 87% of assays, median
  +0.041).
- **Ridge on the store features is the first model that extrapolates.** The embeddings and
  log-probabilities describe a position the model never saw, so `seq` beats zero-shot under
  every scheme, most under random (+0.299) and least under contiguous (+0.060). Adding
  structure and SaProt (`seq_struct`) helps most where extrapolation is hardest: +0.076 over
  `seq` under contiguous, +0.009 under random; it also beats SaProt's own zero-shot score
  under every scheme (+0.092 to +0.263).

## Leaderboard: a sanity range, not a gate

ProteinGym's one-hot baselines (`Supervised_Summary_performance_DMS_substitutions_Spearman.csv`,
pinned commit) use other zero-shot models (ESM-1v, DeepSequence, MSA Transformer, Tranception,
TranceptEVE; no ESM-2) and their own learner, so only the order of magnitude is checked.

| Ours | Published | random | modulo | contiguous |
|---|---|---:|---:|---:|
| one-hot ridge | One-Hot Encodings | 0.608 vs 0.582 | 0.006 vs 0.022 | −0.014 vs 0.059 |
| augmented ridge | ESM-1v + One-Hot | 0.676 vs 0.565 | 0.408 vs 0.394 | 0.409 vs 0.396 |
| augmented ridge | range of the 5 "+ One-Hot" | 0.676 vs 0.523–0.565 | 0.408 vs 0.393–0.435 | 0.409 vs 0.395–0.435 |

Per assay our one-hot ridge tracks theirs closely under random (Spearman 0.96 over 217 assays)
and not at all under modulo / contiguous (0.05, −0.00), as expected of a ranking of fold
constants. Augmented ridge sits inside the published range under modulo and contiguous and
tracks it per assay (0.61–0.73).

**Augmented ridge under random is 0.11–0.15 above every published "+ One-Hot" baseline.** A
number that much better was treated as a leak until shown otherwise:

- Replacing one test fold's labels with noise (×100) leaves that fold's predictions
  bit-identical, on 4 real assays × 5 random folds (20 of 20), as in the unit test.
- The canary passes on all 9 cases with every one informative (below).
- The gain decomposes: augmented ≈ one-hot + the score, and our one-hot alone matches
  ProteinGym's one-hot (0.608 vs 0.582).
- The gap is on the published side: their augmented baselines under random (0.523–0.565) are
  *below their own plain one-hot* (0.582), so their learner loses part of the one-hot signal
  when the score is added. Ours cannot: the score is unpenalised and the one-hot block is
  unchanged, so at worst it reduces to one-hot ridge.

## Penalty choice: edge hits are plateaus

| Run | α at 10⁻³ | α at 10⁶ | median α |
|---|---:|---:|---:|
| one-hot ridge | 1,196 / 3,249 (random 562, modulo 279, contiguous 355) | 1,019 (13, 483, 523) | 10^−0.5 |
| augmented ridge | 1,079 (482, 252, 345) | 489 (11, 180, 298) | 10^0 |
| ridge `seq` | 391 (71, 152, 168) | 48 | 10^2 |
| ridge `seq_struct` | 291 / 3,189 (58, 102, 131) | 37 (3, 18, 16) | 10^2 |

Each is a flat end of the inner-CV error, not a grid that is too short:

- *One-hot, random, low end:* every (position, mutant) column has one row, so a test variant
  is predicted from its position's wild-type column only; as α → 0 that converges.
- *One-hot / augmented, modulo / contiguous, high end:* held-out predictions of unseen positions
  barely depend on α; ties go to the larger α.
- *`seq`, low end:* 332 of the 391 are fits with fewer training rows than columns, where ridge
  tends to the minimum-norm solution as α → 0.

Check: rerunning with the grid widened to 10⁻⁶ … 10⁹ (31 values) changes the pooled Spearman by
at most 0.0010 (augmented, 31 assays, every scheme), 0.0003 (one-hot under random) and < 0.00005
(`seq`, the 20 assays with the most low-end hits). One-hot under modulo / contiguous moves more
(up to 0.56 on one assay), because there it ranks five fold constants that any change reorders.

## Canary on the real models

`train canary`, 3 assays × 3 schemes, 20 permutations each, pass at |z| ≤ 4:

| Run | Passed | Informative (true-label ρ ≥ 0.2) | Largest \|z\| |
|---|---:|---:|---:|
| one-hot ridge | 9 / 9 | 3 (random only) | 1.90 |
| augmented ridge | 9 / 9 | 9 | 3.49 |
| ridge `seq` | 9 / 9 | 9 | 2.81 |
| ridge `seq_struct` | 9 / 9 | 9 | 3.13 |

One-hot ridge learns nothing under modulo / contiguous, so it passes there without proving
anything. The largest |z| of each of the other three is `NPC1_HUMAN_Erwood_2022_HEK293T` under
modulo, always negative (−3.49, −2.81, −3.13): a negative bias of the shuffled-label CV on that
assay. A leak pushes the mean up.

## Alternatives rejected

- **The 40-column wild-type / mutant one-hot (`onehot`) as "one-hot".** It has no position, so it
  is not Hsu et al.'s encoding and could not be set against the leaderboard.
- **A dense or `scipy.sparse` site one-hot.** Dense, even restricted to measured positions, MSH2 alone is 16,749 × 18,680 = 2.5 GB in
  float64. scipy is
  untyped under pyright strict, and two index arrays carry the same information.
- **A tiny penalty on the augmented score (Hsu et al.'s implementation).** Exactly zero is the
  limit, costs nothing with the projection, and needs no extra constant.
- **Random inner folds, or leave-one-out (GCV).** They tune α for interpolation; under modulo and
  contiguous the test asks for extrapolation.
- **The Phase 2 zero-shot numbers as the comparator.** They are not pooled over the supervised
  folds; the `zero_shot` run differs from them by at most 0.002 (0.412–0.414 vs 0.4125 for ESM-2,
  0.458–0.461 vs 0.4600 for SaProt), but pairing needs the same rows.

## Consequences

- Phase 3's next rungs (LightGBM, MLP) plug into the same `fit(x, y, groups)` interface; their
  Optuna search runs inside `groups` like ridge's α.
- `train cv`, `train canary`, `train compare` and `train leaderboard` are CLI commands but not
  Nextflow processes yet, like `data folds` and `features build` (ADR 0019, 0020).
- Imputation of the 6,294 structure NaNs (unmeasured residues outside 3 partial structures) is
  still not needed: no measured variant has one, and the ridge only sees measured variants.
