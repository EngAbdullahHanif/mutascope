# 0023 — MLP head: fixed design, early stopping split like the outer scheme, MSE and RankNet

- Status: accepted
- Date: 2026-10-16
- Builds on: ADR 0019 (CV loop, canary), ADR 0020 (feature store), ADR 0021 (ridge, inner CV split
  like the outer scheme), ADR 0022 (LightGBM, train-only PCA, `--jobs`, checkpoint)

## Context

The third rung of the Phase 3 ladder is a small neural network on the same `seq_struct` features
as ridge and LightGBM, trained with two losses: mean squared error and a pairwise ranking loss
(Spearman is the headline metric, so a loss that only cares about order is the natural
alternative). Its design had to be fixed in config before any result, with early stopping that
never sees the test fold and that mimics the outer scheme, and a minimum assay size chosen
without MLP test results.

## Decision

### 1. The design, fixed in `conf/train/default.yaml` before any MLP result

`models.mlp.EarlyStoppedMLP`, registered as `mlp_mse` and `mlp_rank` (one class; the params differ
only in `loss`):

| Setting | Value | Reason (one line, as in the config) |
|---|---|---|
| Features | `seq_struct`, each 1,280-column embedding → 32 PCA components | As `lgbm_pca`; 2,647 → 151 columns |
| Scaling | every column standardised (mean, population SD) | One scale for log-probabilities, embeddings, one-hots |
| Network | LayerNorm → 512 → GELU → Dropout 0.2 → 128 → GELU → 1 | Given; ~143k parameters |
| Optimiser | AdamW, learning rate 1e-3 | Adam's standard rate; stable without warm-up |
| Weight decay | 1e-2 | PyTorch's AdamW default; dropout and early stopping do most of the regularising |
| Batch size | 128 | The smallest kept assay still gets 2 updates per epoch; a RankNet batch holds up to 8,128 pairs |
| Max epochs | 200 | A ceiling; chosen epochs are written per fold, so hitting it is visible |
| Patience | 20 | 10% of the ceiling: rides out noisy epochs on ~65 validation variants |
| Seed | `train.seed` (0) | Seeds validation-fold choice, initial weights, batch order and dropout |

Nothing is searched. No hyperparameter was changed after the pilot or the full run.

**Plain torch, not Lightning.** CLAUDE.md's layout names Lightning for the MLP. A 30-line loop does
the same job with no new dependency, and it writes no checkpoint files and no per-run logs (the
task requires neither). The best weights are kept in memory.

### 2. Two losses

- `mse`: mean squared error on the standardised `DMS_score` (the loop's target, ADR 0019).
- `rank`: RankNet's pairwise logistic loss (Burges et al., 2005), `log(1 + exp(−(s_i − s_j)))` over
  every pair in a batch with `y_i > y_j`; tied pairs are left out, and a batch with no ordered
  pair takes no step. **Pairs are only ever within one assay**, because a model is fit on one
  assay's training rows (the CV loop calls `fit` per assay and fold); scores of different assays
  are never ranked against each other. Its outputs have no scale, so its MSE in the results file
  is not meaningful; Spearman is.

### 3. Early stopping on one training fold, split like the outer scheme, then refit

`fit(x, y, groups)` gets each training row's outer fold. One of those folds (chosen with the seed)
is the validation set and the network trains on the rest. So the validation split is the outer
scheme's own: a random subset under `random`, positions ≡ k mod 5 under `modulo`, a contiguous
block under `contiguous`. Under the last two, no validation position is in the network's training
rows, as for the test fold (a test checks the position sets are disjoint exactly then). After
every epoch the training loss on all validation rows (no dropout) is computed; training stops 20
epochs after it last improved.

The model is then **refit on all training rows** for the best epoch count, as LightGBM is refit for
its early-stopped rounds and ridge for its chosen α. Every rung's final model therefore sees the
same rows (80% of the assay); keeping the early-stopped network would train on 60%. This was
decided before any result.

### 4. Preprocessing is fitted on the rows each network trains on

The PCA and the standardisation are fitted on the inner-training folds for the early-stopping run
and on all training rows for the refit. Rows a network is evaluated on never shape them; the test
fold never reaches `fit` at all.

**Guards** (`tests/models/test_mlp.py`): changing every label of one test fold (permuted; ×7 − 3)
leaves its predictions bit-identical, for both losses under all three schemes; the early run's
standardiser equals one fitted on the inner-training rows alone.

### 5. Process rules: one thread, seeded, torch only inside `fit`

torch is imported inside `fit` and `predict`, so importing the CLI or the registry still loads
neither torch nor LightGBM (`tests/test_openmp_isolation.py` now includes `models.mlp`). `fit` and
`predict` run with `torch.set_num_threads(1)` (restored afterwards), inside a forked torch random
state seeded with `seed`, so a fit depends only on its inputs and seed, not on the process or
what ran before (tested). `train cv --jobs`, the checkpoint and the memory budget are ADR 0022's,
unchanged.

### 6. Minimum assay size: 300 variants, from data size and the ridge / LightGBM results only

`train.min_variants` (per model; a model not listed has none) is applied by `train cv`, which skips
smaller assays and prints them. It was set from the variant counts and the Task 4/5 results, before
any MLP run:

- **Data size.** At 300 variants the refit trains on 240 and early stopping judges ~60
  validation variants (one training fold), where a Spearman has a standard error near 0.13. Below
  that, early stopping is mostly noise, for a network with ~143k parameters.
- **Ridge and LightGBM by assay size** (`seq_struct`, mean paired difference against zero-shot
  SaProt on the same folds):

| Variants | Assays | ridge − SaProt (random / modulo / contiguous) | `lgbm_pca` − SaProt |
|---|---:|---|---|
| ≤ 200 | 9 | +0.140 / −0.161 / −0.203 | +0.160 / −0.140 / −0.199 |
| 201–300 | 6 | +0.111 / +0.099 / −0.001 | +0.123 / +0.074 / −0.060 |
| 301–400 | 6 | +0.205 / +0.136 / +0.127 | +0.209 / +0.052 / +0.071 |
| 501–750 | 15 | +0.263 / +0.151 / +0.133 | +0.265 / +0.062 / +0.066 |
| 751–1,000 | 21 | +0.270 / +0.168 / +0.142 | +0.288 / +0.107 / +0.056 |
| 1,001–2,000 | 74 | +0.282 / +0.177 / +0.155 | +0.303 / +0.144 / +0.125 |
| 2,001–5,000 | 38 | +0.268 / +0.143 / +0.096 | +0.301 / +0.154 / +0.098 |
| > 5,000 | 44 | +0.295 / +0.180 / +0.121 | +0.320 / +0.210 / +0.151 |

  (No structure assay has 401–500 variants.) Below 300, both supervised models lose to zero-shot on
  unseen positions; from 300 up, both beat it on average in every bin and scheme, and ridge beats
  it in every 301–400 assay under every scheme.

**15 of the 213 structure assays (2,530 variants) are skipped; the MLP runs on 198 (663,263
variants).** Skipped assays keep their ridge and LightGBM results. `train compare` allows a run with
a minimum to lack exactly the assays below it (the reference file's single-mutant counts, equal to
the processed counts in every assay), so the MLP is paired on its 198 assays; any other missing
assay is still an error (tested). The canary assays (326–637 variants) are all kept; `train canary`
refuses an assay below the minimum.

### 7. Pilot (Step 1): time and seed-to-seed spread

10 assays at size deciles of the 198 kept (637–12,003 variants), both losses, seeds 0, 1, 2, all
three schemes, 10 workers:

- **Wall time** 59–68 s per run (6.7 min for all six). Per assay (all schemes) about 0.0044 s
  (MSE) and 0.0050 s (rank) per variant, plus 6–9 s. Extrapolated to the full run (198 assays,
  both losses, one seed): ~9,100 worker-seconds, **12–16 minutes** on 13 workers, against a
  10-hour stop line. The actual full run took 558 s (MSE) + 486 s (rank).
- **Seed-to-seed spread of pooled Spearman** (3 seeds): per assay, median SD 0.002–0.003 under
  random, 0.006–0.009 under modulo, 0.012–0.014 under contiguous (worst 0.034, MSE, contiguous).
  The plain mean over the 10 assays differs between seeds by at most 0.003 (random), 0.005
  (modulo) and 0.013 (contiguous). The full run uses one seed (0); a paired difference smaller
  than ~0.01 under contiguous is within what a reseed could move.

## Results (ProteinGym v1.3, `seq_struct`, pooled Spearman, ProteinGym aggregate, 198 assays)

| Run | random | modulo | contiguous |
|---|---:|---:|---:|
| ridge `seq_struct`, same 198 assays | 0.736 | 0.621 | 0.581 |
| `lgbm_pca` `seq_struct`, same 198 assays | 0.759 | 0.607 | 0.562 |
| zero-shot SaProt 650M, same 198 assays | 0.459 | 0.459 | 0.460 |
| **`mlp_mse`** | 0.762 [0.746, 0.779] | 0.622 [0.604, 0.640] | 0.570 [0.551, 0.592] |
| **`mlp_rank`** | 0.762 [0.747, 0.779] | 0.613 [0.596, 0.631] | 0.565 [0.546, 0.585] |

Brackets: each run's own 95% bootstrap CI; it does not compare runs. On all 213 assays ridge is
0.721 / 0.593 / 0.553 (ADR 0021): the 15 skipped assays are ones supervised models do badly on,
so the MLP's aggregate must never be set against a 213-assay number. Pooled MSE (training-SD units,
same 198 assays): `mlp_mse` 0.363 / 0.595 / 0.709, ridge 0.407 / 0.584 / 0.670, `lgbm_pca` 0.361 /
0.587 / 0.678; the rank model's MSE is not meaningful.

**Paired** (`A − B`, 198 assays, 10,000 resamples; "A > B" = share of resamples):

| A − B | random | modulo | contiguous |
|---|---:|---:|---:|
| `mlp_mse` − ridge `seq_struct` | **+0.026** [+0.022, +0.029] | +0.001 [−0.005, +0.007] (61%) | **−0.011** [−0.019, −0.003] |
| `mlp_rank` − ridge `seq_struct` | **+0.026** [+0.021, +0.031] | **−0.008** [−0.014, −0.002] | **−0.016** [−0.025, −0.008] |
| `mlp_mse` − `lgbm_pca` `seq_struct` | +0.003 [+0.001, +0.005] | **+0.016** [+0.008, +0.022] | +0.008 [−0.001, +0.016] (96%) |
| `mlp_rank` − `lgbm_pca` `seq_struct` | +0.003 [+0.001, +0.006] | +0.007 [−0.000, +0.013] (97%) | +0.003 [−0.005, +0.011] |
| `mlp_mse` − zero-shot SaProt | +0.303 [+0.281, +0.323] | +0.163 [+0.147, +0.178] | +0.111 [+0.094, +0.126] |
| `mlp_rank` − zero-shot SaProt | +0.303 [+0.282, +0.323] | +0.154 [+0.139, +0.168] | +0.105 [+0.091, +0.118] |
| `mlp_rank` − `mlp_mse` | +0.000 [−0.002, +0.003] | **−0.009** [−0.012, −0.005] | −0.006 [−0.011, +0.000] |

Per assay, `mlp_mse` beats ridge in 176 / 198 assays under random (median +0.018), 97 under modulo
(median −0.002) and 74 under contiguous (median −0.009).

### What the numbers mean

- **The MLP does not beat ridge on unseen positions.** It gains +0.026 on seen positions (random),
  like LightGBM (+0.024), but under modulo it ties ridge (MSE, interval across 0) or loses (rank),
  and under contiguous both losses are below ridge with intervals excluding 0. The ladder's third
  rung repeats the second's pattern: more flexible models fit seen positions better and extrapolate
  no better than a linear model on the same features.
- **It does beat LightGBM where LightGBM was weakest**: +0.016 under modulo for MSE (interval
  excludes 0), +0.008 under contiguous (interval reaches 0). A smooth function of the embeddings
  extrapolates better than boxes in them; still not better than a straight line.
- **The ranking loss does not help.** RankNet equals MSE under random and is worse under modulo
  (−0.009, interval excludes 0) and contiguous (−0.006, reaching 0). Optimising order alone throws
  away the magnitude information MSE uses, and early stopping on the pairwise loss stopped earlier
  (median 3 epochs vs 5–8 under modulo / contiguous).
- **Both beat zero-shot SaProt under every split** (+0.105 to +0.303).
- **Early stopping stops early.** Median 6 epochs over 5,940 fits (MSE: 16 random, 8 modulo, 5
  contiguous; rank: 9 / 3 / 3); 1,261 stopped at epoch 1–2, none reached 200 (max 198). With
  ~143k parameters and hundreds to thousands of rows the validation loss turns within a few
  epochs; on unseen positions sooner. This is the fixed design's behaviour and was not tuned.
- Nothing here is "surprisingly good": the random-split gain is the size of LightGBM's, and the
  canary passes (below).

**For deployment** (ADR 0022's rule: choose by modulo and contiguous, the splits that match
predicting a full landscape): ridge `seq_struct` stays the deployed candidate. `mlp_mse` ties it
under modulo and is 0.011 below it under contiguous.

## Canary on the MLP models

`train canary`, 3 assays × 3 schemes, 20 permutations, pass at |z| ≤ 4:

| Run | Passed | Informative (true-label ρ ≥ 0.2) | Largest \|z\| |
|---|---:|---:|---:|
| `mlp_mse` `seq_struct` | 9 / 9 | 9 (ρ 0.57–0.89) | 2.56 (ERBB2, modulo) |
| `mlp_rank` `seq_struct` | 9 / 9 | 9 (ρ 0.63–0.88) | 1.65 (ERBB2, contiguous) |

**A canary pitfall specific to networks.** In the CLI test, with a 6-epoch budget, the canary
failed on a synthetic assay (permuted mean +0.154 ± 0.019, z = +7.9) with no leak. Trained on
shuffled labels for that little, a network stays near its seeded initial weights: one fixed random
function of features that carry signal (the seed-0 untrained network alone has ρ = −0.28 with the
synthetic labels; reseeding swings permuted results between −0.59 and +0.45). With one seed that
offset is the same in every permutation, so the mean drifts from 0. At the configured budget the
canary passes on the same synthetic assay (4 cases, both losses, random and contiguous, |z| ≤ 0.86) and on the real ones above. A
zero-initialised output layer (the untrained network predicts the training mean) was tried and not
adopted: at the configured budget it changed no canary verdict and lowered true-label Spearman
(0.769 → 0.709, synthetic, contiguous). The test now trains with the real epoch budget.

## Alternatives rejected

- **Keeping the early-stopped network** (no refit): trains on 60% of the assay instead of 80%,
  unlike every other rung.
- **A random validation split for every scheme**: tunes the stopping point for interpolation; the
  modulo and contiguous test folds ask for extrapolation (ADR 0021's argument).
- **Early stopping on validation Spearman**: noisier per epoch than the loss on ~65 rows; each
  model stops on its own objective.
- **PyTorch Lightning**: a dependency for a 30-line loop, plus checkpoint and log files to suppress.
- **Pairs across assays in the ranking loss**: DMS scores of different assays are on different
  scales and selections; the per-assay protocol has one assay per model anyway.
- **Choosing the minimum assay size from MLP results**: would tune the benchmark on the test folds.

## Consequences

- No new dependency (torch was already one).
- `train.min_variants` is a general per-model setting; `train compare` pairs a run with a minimum
  on the assays it has, and nothing else may be missing.
- Seed-to-seed spread is now measured (pilot); differences under contiguous below ~0.01 should be
  read with it in mind.
- `train cv`, `train canary` for the MLP are CLI commands, not yet Nextflow processes.
