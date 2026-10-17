# 0026 — Phase 3 close: supervised models

- Status: accepted
- Date: 2026-10-17
- Changes: nothing. Carries one open Phase 3 item (Hub publication) into Phase 4 (see "Open and
  weak")

## Context

Phase 3 (weeks 5–6) had one goal: beat zero-shot using the assay data, under ProteinGym's
supervised protocol. Its done-when line has four parts: a Spearman table (3 splits × 4 rungs
against zero-shot, bootstrap CIs), a SHAP figure, a calibration plot, and model cards on the
Hugging Face Hub. The table, the SHAP figure and the calibration plot exist. The model cards are
written and staged in `build/` but **not on the Hub**: this machine has no Hugging Face token
(`hf auth whoami`: "Not logged in"). That item stays open and is listed below. This record
summarises what exists, what is weak, and what Phase 4 builds on. Details are in ADRs 0019–0025
and build-log entries 19–25.

## What Phase 3 produced

**Headline table.** Pooled Spearman per assay (ProteinGym's supervised metric: each test fold's
predictions pooled, ADR 0019), aggregated ProteinGym-style (per UniProt ID, then per function
type, then overall), with 95% bootstrap CIs over assays (10,000 resamples, seed 0, stratified by
function type). The first value in a row is the run's own aggregate with its own CI; the second
line is the paired bootstrap of run − comparator on the same assays and test folds. The
comparator is the best zero-shot model the run can face: SaProt 650M for a structure feature set
(213 assays; 198 for the MLP), ESM-2 650M otherwise (217), each run through the same CV loop
(ADR 0021, 0025). Read from `results/supervised/final_summary.parquet` and
`final_comparisons.parquet`; augmented ridge, which is not in the final table, from
`supervised_summary.parquet` and `supervised_comparisons.parquet`.

| Run (rung) | Assays | Comparator | random | modulo | contiguous |
|---|---:|---|---:|---:|---:|
| zero-shot ESM-2 650M | 217 | — | 0.412 [0.389, 0.441] | 0.412 [0.388, 0.442] | 0.414 [0.390, 0.444] |
| zero-shot SaProt 650M | 213 | — | 0.458 [0.441, 0.481] | 0.458 [0.440, 0.483] | 0.461 [0.441, 0.485] |
| one-hot ridge (ridge) | 217 | ESM-2 | 0.608 [0.587, 0.635] | 0.006 [−0.014, 0.022] | −0.014 [−0.043, 0.011] |
| | | paired | +0.196 [+0.158, +0.234] | −0.406 [−0.442, −0.379] | −0.429 [−0.473, −0.394] |
| augmented ridge (ridge) | 217 | ESM-2 | 0.676 [0.660, 0.698] | 0.408 [0.384, 0.438] | 0.409 [0.383, 0.439] |
| | | paired | +0.264 [+0.233, +0.294] | −0.003 [−0.006, −0.001] | −0.005 [−0.009, −0.003] |
| ridge `seq` (ridge) | 217 | ESM-2 | 0.711 [0.695, 0.732] | 0.538 [0.511, 0.564] | 0.475 [0.444, 0.507] |
| | | paired | +0.299 [+0.267, +0.329] | +0.126 [+0.093, +0.151] | +0.060 [+0.027, +0.090] |
| **ridge `seq_struct` (ridge, deployed)** | 213 | SaProt | 0.721 [0.705, 0.741] | 0.593 [0.571, 0.617] | 0.553 [0.528, 0.581] |
| | | paired | +0.263 [+0.239, +0.286] | +0.135 [+0.109, +0.156] | +0.092 [+0.065, +0.115] |
| LightGBM-PCA `seq` (LightGBM) | 217 | ESM-2 | 0.731 [0.714, 0.752] | 0.531 [0.506, 0.557] | 0.464 [0.436, 0.495] |
| | | paired | +0.318 [+0.286, +0.349] | +0.119 [+0.090, +0.143] | +0.050 [+0.022, +0.073] |
| LightGBM-PCA `seq_struct` (LightGBM) | 213 | SaProt | 0.745 [0.730, 0.763] | 0.582 [0.559, 0.605] | 0.532 [0.503, 0.565] |
| | | paired | +0.287 [+0.263, +0.309] | +0.124 [+0.099, +0.143] | +0.072 [+0.043, +0.096] |
| MLP (MSE) `seq_struct` (MLP) | 198 | SaProt | 0.762 [0.746, 0.779] | 0.622 [0.604, 0.640] | 0.570 [0.551, 0.592] |
| | | paired | +0.303 [+0.281, +0.323] | +0.163 [+0.147, +0.178] | +0.111 [+0.094, +0.126] |
| MLP (RankNet) `seq_struct` (MLP) | 198 | SaProt | 0.762 [0.747, 0.779] | 0.613 [0.596, 0.631] | 0.565 [0.546, 0.585] |
| | | paired | +0.303 [+0.282, +0.323] | +0.154 [+0.139, +0.168] | +0.105 [+0.091, +0.118] |

How to read it:

- **random** puts variants at a position into both training and test folds (interpolation);
  **modulo** and **contiguous** hold out whole positions, so the model predicts at positions it
  has never seen (extrapolation). The website's full landscape is mostly the second task.
- Every supervised model with real features beats its zero-shot comparator under every split,
  and every one of those paired intervals excludes 0. The gain is three to six times larger when
  the position was seen in training (random) than when it was not (contiguous).
- One-hot ridge and augmented ridge are the protocol's reference baselines: with no features
  that describe a residue, they cannot predict an unseen position (one-hot) or reduce to the
  zero-shot score with a learned sign (augmented, ADR 0021).
- The 213-, 217- and 198-assay rows are on different assays. Runs are compared only by paired
  bootstraps on shared assays (below, and `supervised_comparisons.parquet`), never by their own
  CIs. The MLP's 198 assays leave out the 15 smallest, where supervised models do worst
  (ADR 0023).
- These are predictions of functional effect in DMS assays. Supervised models were trained on
  each assay's own measurements; nothing here is clinical.

**Per function type** (deployed ridge `seq_struct` − SaProt, paired, 213 assays):

| Function type | Assays | random | modulo | contiguous |
|---|---:|---:|---:|---:|
| Activity | 42 | +0.202 [+0.159, +0.238] | +0.120 [+0.087, +0.147] | +0.090 [+0.063, +0.113] |
| Binding | 13 | +0.287 [+0.194, +0.380] | +0.076 [−0.033, +0.172] | +0.010 [−0.106, +0.121] |
| Expression | 18 | +0.242 [+0.195, +0.285] | +0.120 [+0.089, +0.148] | +0.071 [+0.040, +0.101] |
| OrganismalFitness | 74 | +0.284 [+0.245, +0.322] | +0.172 [+0.134, +0.202] | +0.126 [+0.092, +0.153] |
| Stability | 66 | +0.300 [+0.280, +0.319] | +0.187 [+0.170, +0.203] | +0.162 [+0.145, +0.179] |

**The rungs against each other** (paired, `supervised_comparisons.parquet`):

| A − B | Assays | random | modulo | contiguous |
|---|---:|---:|---:|---:|
| ridge `seq_struct` − ridge `seq` | 213 | +0.009 [+0.007, +0.010] | +0.054 [+0.042, +0.068] | +0.076 [+0.061, +0.092] |
| LightGBM-PCA `seq_struct` − ridge `seq_struct` | 213 | +0.024 [+0.019, +0.028] | −0.011 [−0.021, −0.003] | −0.020 [−0.037, −0.005] |
| MLP (MSE) − ridge `seq_struct` | 198 | +0.026 [+0.022, +0.029] | +0.001 [−0.005, +0.007] | −0.011 [−0.019, −0.003] |
| MLP (RankNet) − MLP (MSE) | 198 | +0.000 [−0.002, +0.003] | −0.009 [−0.012, −0.005] | −0.005 [−0.011, +0.000] |

Structure features help most where the position is unseen. The non-linear models are better
only when the position was seen in training. A ranking loss does not help.

**Deployment decision.** The deployed model is **ridge `seq_struct`**: one fixed ridge regression
per assay, the same model everywhere, for the **198 assays that have a structure and at least 300
measured variants**. The rule was fixed before the final table (ADR 0022, 0023, 0025):

1. *Choose on the splits that match the use.* A full landscape is almost entirely predictions at
   positions without measurements (585,637 of the 716,726 unmeasured substitutions in the
   bundle), so the model is chosen by modulo and contiguous, not random. There ridge beats
   LightGBM (−0.011 and −0.020 for LightGBM, both intervals below 0) and ties or beats the MLP
   (+0.001 with an interval across 0 under modulo; −0.011 for the MLP under contiguous, interval
   below 0). The MLP and LightGBM win under random, and that is not the job the website does.
2. *Supervision only where it pays.* Below 300 variants no supervised model's interval on unseen
   positions is above SaProt's, and below 200 ridge is clearly worse under contiguous (−0.244
   [−0.444, −0.046], ADR 0025's plain-mean by-size table). From 300 up every one is above.
3. *Everyone else gets zero-shot.* The 19 ineligible assays show SaProt (15 small assays with a
   structure) or ESM-2 650M (the 4 without one), labelled zero-shot. Nothing is chosen per assay
   from its own results.
4. *Each prediction carries an honest range.* 80% and 90% CV+ intervals, constant width,
   calibrated on folds that mimic the task: random folds at positions with measurements,
   contiguous folds at positions without (ADR 0024, 0025). On held-out test folds these covered
   0.908–0.911 at 90% under every split (ADR 0024). Measured variants show their out-of-fold
   prediction, never the model's fit to their own label.

**Deployed bundle** (`train final`, ADR 0025; `results/supervised/final_checks.parquet`,
`final_manifest.parquet`): 1,379,989 substitutions over 198 assays (663,263 measured, 131,089
unmeasured at measured positions, 585,637 at unmeasured positions), stamped with code `6fb0d07`
and the config and data-manifest hashes. Release checks rerun from the files for this record:
no NaN, no interval with lower ≥ upper, CV+ refit against the saved calibration rows maximum
difference 0, out-of-fold pooled Spearman against `cv_ridge_seq_struct.parquet` maximum
difference 0 / 3.5e-7 / 6.6e-8 (random / modulo / contiguous). Sanity check: buried residues are
predicted lower than exposed ones in 194 of 195 assays that have both.

**Protocol checks behind the numbers.** No hyperparameter, scaler, PCA or Optuna search sees a
test fold (nested CV split like the outer scheme; leakage tests make a test fold's predictions
bit-identical when its labels are replaced). The permutation canary passed for every real model
(8 canary files). ProteinGym's supervised leaderboard was used as a sanity range, not a gate
(ADR 0021): one-hot ridge tracks the published one-hot per assay (Spearman 0.96 under random);
augmented ridge under random is 0.11–0.15 above the published "+ One-Hot" baselines, which was
investigated as a possible leak and traced to their learner (ADR 0021).

**Figures** (`results/figures/`, from results files only): `final_ladder`, `final_by_size`,
`shap_lgbm_seq_struct`, `calibration_ridge_seq_struct`, `width_error_ridge_seq_struct`.

**In git.** The `features` package (store, sets, matrices, embeddings), the `models` package
(CV loop, ridge, LightGBM, MLP, CV+, final fits), `eval.supervised` and `eval.final`,
`utils.parallel`, Hydra groups `features` and `train`, every file under `results/supervised/` and
the figures above, `docs/MODEL_CARD.md`, the updated `docs/DATA_CARD.md`, ADRs 0019–0026. Not
in git: the feature store and embedding cache, the calibration rows
(`data/processed/calibration/`), the deployed landscapes
(`data/processed/supervised_landscapes/`, 68 MiB), the staged Hub repos (`build/hf_model` 9 MiB,
`build/hf_dataset` 68 MiB) and the MLflow store (34 supervised runs, 2 feature runs).

## Open and weak

Unchecked roadmap items:

1. **Publication to the Hub.** `train publish --push` and `data publish --landscapes --push`
   are staged and tested but not run: there is no Hugging Face token on this machine. Until
   then the done-when part "model cards on the HF Hub" is not met. The Phase 1 dataset repo
   (`engAbdullahHanif/mutascope-proteingym-v1.3`, private) was marked published in `54f6ef0`;
   the landscapes go to it as a new revision and wait for the same login. Moves to Phase 4,
   where the Space needs the Hub anyway.
2. **LoRA fine-tuning of ESM-2 150M and a leave-protein-out global model** (stretch). Not
   started.
3. **Carried from earlier phases:** the `docker` pipeline profile (Phase 1, waits for the Phase
   4 image); ESM-1v and ESM C 300M (Phase 2 stretch).

Weak points in what is ticked:

4. **`make pipeline` reproduces none of Phase 3.** `data folds`, `features embed`, `features
   build` and every `train` command are CLI commands run by hand; no Nextflow process calls
   them (ADR 0019–0025 each say so). The committed results come from those hand runs, and their
   MLflow runs record the configs.
5. **`main` was red for two merges.** CI failed after PR #24 and PR #25 were merged:
   `test_parallel_assays_give_the_serial_table` disagreed by up to 3.4e-3 on Linux. Fixed in
   `e6592db` (PR #26, green since); build-log entry 26 records the cause. CLAUDE.md requires
   `main` to stay green.
6. **CI trains nothing real.** Tests use synthetic data and `esm2_t6_8M`. The only checks that
   the committed results can be reproduced are the ones inside `train final` (refit against
   `train calibrate`, out-of-fold Spearman against `train cv`), and they run on the dev machine.
7. **Binding rests on 13 assays, and supervision is not shown to help it on unseen positions.**
   Ridge `seq_struct` − SaProt includes 0 under modulo (+0.076 [−0.033, +0.172]) as well as
   contiguous (+0.010 [−0.106, +0.121]). ADR 0025 named only the contiguous case.
8. **The deployed model is not the best model on every split.** MLP (MSE) is +0.026 over it
   under random and LightGBM-PCA +0.024. The choice is right for the landscape, but a user
   looking only at measured positions would be better served by the MLP.
9. **Intervals do not say which variants are harder.** The deployed intervals have constant
   width per assay and scheme. Adaptive intervals over-covered by 4–5 points at 35–43% more
   width, and the fold-ensemble spread ranks error only weakly (median Spearman 0.08–0.17;
   ADR 0024).
10. **The MLP has no intervals.** Its CV+ design was not run (16.2× a plain CV fit, ~2.5 h;
    ADR 0024). Harmless for deployment, but the uncertainty result covers ridge only.
11. **Coverage of measured variants (0.800 / 0.900) holds by construction** and proves nothing
    (ADR 0025). The evidence is ADR 0024's test-fold coverage.
12. **Two deployed targets are mostly outside their structure.** 108,452 rows (ZIKV 55,461 of
    65,037; POLG_HCVJF 52,991 of 57,627) use mean-imputed structure features, flagged
    `structure_imputed`.
13. **Thin LightGBM coverage.** SHAP is on 3 assays; `lgbm` on `seq` was not run (~5 h);
    Optuna has 10 trials per outer fold (ADR 0022).
14. **By-size bins use a plain mean,** a deliberate, documented deviation from the ProteinGym
    aggregate (ADR 0025). Every headline here is a ProteinGym aggregate.
15. **CLAUDE.md said "conformal prediction (MAPIE)".** ADR 0024 implemented CV+ directly because
    MAPIE cannot normalise cross-conformal scores or take per-row folds. Corrected with this
    record.
16. **`docs/PROJECT_MAP.md` has no steps 19–25.** It jumps from step 18 to this close-out (step
    26), so Phase 3's files are indexed only through the build log and ADRs.
17. **Two rounding slips in accepted ADRs**, left as written: ADR 0021 gives augmented ridge −
    zero-shot under contiguous as −0.006 (the file says −0.005483), ADR 0022 gives
    LightGBM-PCA `seq` contiguous as 0.465 (0.464473; found in build-log entry 25).

None of these changes a number in the tables above.

## Decisions that mattered most

1. **Choose the deployed model by the task it will do (ADR 0022).** The random split is the
   easiest to win and the one most papers lead with; LightGBM and the MLP win it. The website
   predicts at unmeasured positions, which the modulo and contiguous splits test, and there a
   linear model on rich features is as good or better. The rule was written down after the
   LightGBM result and before the MLP and final results, and it held.
2. **Compare on the same folds, with a fair comparator, always paired (ADR 0021, 0025).** The
   zero-shot models were run through the same CV loop, so every supervised number is set against
   zero-shot on exactly the same test folds; structure runs face SaProt, not the weaker ESM-2;
   and every claim that one run beats another is a paired bootstrap on shared assays with the
   assay count stated.
3. **Fix the uncertainty design before any result, and block the release on reproduction
   (ADR 0024, 0025).** Interval levels, calibration modes and widths were fixed before the first
   coverage number and judged on test folds the intervals never saw. The deployed bundle is
   written only if a refit reproduces the calibration exactly and the CV results to 1e-6, so the
   published landscapes are the models the tables describe.

## What Phase 4 inherits

- **Supervised landscapes:** `data/processed/supervised_landscapes/ridge_seq_struct/`
  (partitioned by `assay_id`, 1,379,989 rows: prediction, 80% and 90% bounds, `measured`,
  `scheme`, `structure_imputed`, version stamp). Its manifest
  (`results/supervised/final_manifest.parquet`) gives each of the 217 assays its
  `default_model`: `ridge_seq_struct` (198), `saprot_650m_af2` (15), `esm2_650m` (4), with the
  reason.
- **Zero-shot landscapes:** full masked landscapes in the cache for all four models (ADR 0018);
  run `mutascope score check-landscapes` before the export reads them.
- **Labels the frontend must carry:** "Supervised (n = measured variants)" for the 198, "Zero-shot"
  for the rest; intervals shown with their level; imputed-structure rows flagged; the five
  MSA-region assays labelled (ADR 0012).
- **Hub:** `build/hf_model` and `build/hf_dataset` staged; push after `hf auth login`. The
  export may read from the Hub instead of `data/processed/` once they are there.
- **Serving:** supervised predictions are precomputed only; live scoring stays ESM-2 35M / 150M
  through ONNX (CLAUDE.md). No supervised model is served live.
- **Pipeline debt:** the Phase 3 commands as Nextflow processes, and the `docker` profile, belong
  with Phase 4's Docker and CI work.
- **Numerical determinism:** results must not depend on BLAS threading; `utils.parallel` caps
  every loaded BLAS/OpenMP library at one thread in workers and in the serial path, and the
  linux/amd64 image must keep that.
