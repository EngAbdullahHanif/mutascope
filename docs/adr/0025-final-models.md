# 0025 — Final models: results table, one fixed ridge per assay, full landscapes with intervals

- Status: accepted
- Date: 2026-10-16
- Builds on: ADR 0014 (paired bootstrap), ADR 0019 (CV loop), ADR 0021 (ridge), ADR 0022
  (deployment chosen on unseen positions), ADR 0023 (300-variant minimum), ADR 0024 (CV+,
  `train calibrate`)

## Context

Phase 3 ends with three deliverables: a final results table of every supervised run against the
best zero-shot model, figures drawn from results files only, and the deployed model, one fixed
model per eligible assay that predicts all 19 × L substitutions with intervals, published on the
Hugging Face Hub. ADR 0022–0024 had already chosen the model (ridge `seq_struct`: best on unseen
positions) and the interval method (CV+, scheme-like calibration folds, constant width). What was
left to decide: which comparator each run gets, how the final model's penalty is chosen, what a
measured variant shows, how a position's status picks the calibration, what to do where a feature
is missing, and which checks block a release.

## Decision

### 1. Final results table: results only, each run against the best zero-shot model it can face

`train final-table` reads `train cv`'s files and writes four tables to `results/supervised/`:

- `final_summary.parquet`: each run's ProteinGym aggregate of pooled Spearman with its own 95%
  bootstrap CI, overall and per function type, with `n_assays`;
- `final_comparisons.parquet`: the paired bootstrap of `run − zero-shot` on the same test folds
  and the same assays (10,000 resamples, seed 0). The comparator is **SaProt 650M for a structure
  feature set and ESM-2 650M otherwise**: SaProt is the stronger zero-shot model (ADR 0017) but
  exists on 213 assays only, so a 217-assay run is paired with ESM-2. The MLP (198 assays, ADR
  0023) is paired with SaProt on its 198;
- `final_assay_differences.parquet` and `final_size_comparisons.parquet`: per assay, and per
  assay-size bin.

Runs: ridge on `site_onehot`, `seq` and `seq_struct`; `lgbm_pca` on `seq` and `seq_struct`;
`mlp_mse` and `mlp_rank` on `seq_struct` (`train.final_table.runs`). Augmented ridge and the
post-hoc LightGBM diagnostic stay in `supervised_summary.parquet` (ADR 0021, 0022). No training:
the summary and comparisons are bit-identical to `train compare`'s rows for the same runs and
pairs (162 and 126 rows, maximum difference 0.0).

**By-size bins use a plain mean, not the ProteinGym aggregate**: a deliberate deviation, see
"Deviations and caveats" below.

### 2. Figures from results files only

`train final-figures` draws `results/figures/final_ladder` (per scheme, each run's paired
difference from its comparator with the paired CI; the run's own aggregate as text, its own CI
not drawn) and `final_by_size` (per bin and scheme, ridge / LightGBM-PCA / MLP-MSE minus SaProt
with paired CIs; ridge's per-assay differences as dots). The calibration result is Task 7's
`calibration_ridge_seq_struct` (`train uncertainty-figures`, unchanged).

### 3. One fixed model, eligibility by rule

Every eligible assay gets ridge `seq_struct`; nothing is selected per assay. **Eligible: a
structure and at least 300 measured variants** (`train.final.min_variants`; ADR 0023's minimum,
set from ridge and LightGBM results only: below it supervised models lose to SaProt on unseen
positions, and the by-size table above confirms it: −0.244 under contiguous below 200 variants).
198 of 217 assays are eligible. For the other 19 the bundle's manifest stores the reason ("no
structure": 4; "fewer than 300 measured variants (n)": 15) and the website default: SaProt where
a structure exists, ESM-2 650M otherwise (`train.final.zero_shot_default`).

### 4. Two final fits per assay, matched to the position

ADR 0024 says intervals must be calibrated on folds that mimic the prediction task. A landscape
holds two tasks: variants at positions that have measurements (interpolation, like the random
split) and variants at positions with none (extrapolation, like contiguous). The inner-CV
penalty is a choice of the same kind: chosen on random folds it suits interpolation, on
contiguous folds extrapolation, and the two differ by a median of one decade in
`cv_ridge_seq_struct_hyperparameters.parquet` (equal in 18% of folds).

So each assay gets **two final ridges** on all measured variants, `alpha` from inner CV over the
random folds and over the contiguous folds, and **every variant takes one scheme from its
position** (`train.final.scheme_by_position`):

| Variant | Scheme | Prediction | 80% / 90% interval |
|---|---|---|---|
| unmeasured, position has measured variants | random | final ridge (random `alpha`) | CV+ at x, random folds |
| unmeasured, position has none | contiguous | final ridge (contiguous `alpha`) | CV+ at x, contiguous folds |
| measured, position has other measured variants | random | out-of-fold `M_k(x)` | `M_k(x) ± q`, random folds |
| measured, only one at its position | contiguous | out-of-fold `M_k(x)` | `M_k(x) ± q`, contiguous folds |

"Has measured variants" means *other* variants for a measured one: without itself, a lone
measured variant sits at an unseen position. Both choices were confirmed with the author before
the run. The two coefficient sets are both published; a user reproduces a prediction with the fit
named in its row.

### 5. A measured variant shows its out-of-fold prediction and an out-of-fold interval

The final ridge trained on every measured variant, so its value there is an in-sample fit and is
never stored. A measured variant's prediction is `M_k(x)` from the CV+ fold model that never saw
it (the calibration row `train calibrate` saved). Its interval is `M_k(x) ± q`, where `q` is the
CV+ upper order statistic (`⌈level · n⌉`-th smallest) of the **other** n calibration rows'
absolute residuals, its own left out. Plain CV+ at x would use the K − 1 fold models that trained
on it and be optimistically narrow. With one centre, CV+'s lower bound is the same rank from the
other end, so the interval is symmetric (tested against a brute-force leave-one-out).

### 6. Missing structure features: measured-variant mean, flagged

`seq_struct`'s three structure features are missing at residues outside a partial structure
(ADR 0021: 6,294 residues). No measured variant is affected, but two eligible targets are mostly
outside their structure: `A0A140D2T1_ZIKV_Sourisseau_2019` (2,919 of 3,423 residues) and
`POLG_HCVJF_Qi_2014` (2,789 of 3,033). A missing value is replaced by the column's mean over the
assay's measured variants (no labels involved). Ridge standardises columns with nearly that mean,
so the feature contributes almost nothing there; the prediction rests on the sequence and SaProt
features, which exist at every residue (ADR 0018). Rows are flagged `structure_imputed`, and the
imputation value is published with the coefficients.

### 7. The checks that block a release

`train final` refits CV+ on exactly `train calibrate`'s inputs and fails, writing no bundle, if:

- an eligible assay lacks a complete 19 × L landscape, or any prediction or bound is NaN;
- any interval has lower ≥ upper (the schema also requires the 90% interval to contain the 80%);
- the refit CV+ differs from the saved calibration rows by more than 1e-9 (fold labels must be
  identical);
- the out-of-fold pooled Spearman differs from `cv_ridge_seq_struct.parquet` by more than 1e-6
  in any scheme. `train calibrate` standardised targets with all variants, `train cv` per fold,
  so each fold's predictions are mapped back to that fold's training scale before pooling; ridge
  is affine-equivariant in the target, so the two must agree.

Reported, never failed: whether the median predicted score at buried residues (relative SASA
< 0.2) is lower than at exposed ones, and the out-of-fold coverage of measured variants.

A test (`test_cli_final.py`) runs the command on calibration rows made with another penalty grid
and requires the failure and no bundle.

### 8. Version stamp and publication

Every landscape row and the manifest carry `code_version` (git commit, `-dirty` if the code
differs), `config_sha256` (sha256 of the resolved config as canonical JSON) and
`manifest_sha256` (sha256 of `data/manifest.yaml`, which pins every input checksum).

- `train publish` stages a Hugging Face **model** repo (`train.publish.repo_id`): per assay the two
  fits (`alpha`, intercept, target mean / SD) and 2 × 2,647 coefficients in `DMS_score` units on
  raw feature values, imputation values, the manifest, the version, the configs as committed plus
  the resolved one, the final results tables and `docs/MODEL_CARD.md`.
- `data publish --landscapes` adds two configs to the dataset repo, `supervised_landscapes` (one
  file, `assay_id` a column) and `supervised_landscape_assays` (the manifest), and the updated
  `docs/DATA_CARD.md`; the upload is one commit, i.e. a new revision.

## Results

### Final table (ProteinGym v1.3, pooled Spearman, ProteinGym aggregate)

Own value with its 95% bootstrap CI (it describes the run, it does not compare runs), and the
paired difference from the comparator on the same assays (`results/supervised/final_*.parquet`,
figure `final_ladder`):

| Run | Assays | Comparator | random | modulo | contiguous |
|---|---:|---|---:|---:|---:|
| one-hot ridge | 217 | ESM-2 | 0.608 [0.587, 0.635] | 0.006 [−0.014, 0.022] | −0.014 [−0.043, 0.011] |
| | | paired | +0.196 [+0.158, +0.234] | −0.406 [−0.442, −0.379] | −0.429 [−0.473, −0.394] |
| ridge `seq` | 217 | ESM-2 | 0.711 [0.695, 0.732] | 0.538 [0.511, 0.564] | 0.475 [0.444, 0.507] |
| | | paired | +0.299 [+0.267, +0.329] | +0.126 [+0.093, +0.151] | +0.060 [+0.027, +0.090] |
| **ridge `seq_struct`** | 213 | SaProt | 0.721 [0.705, 0.741] | 0.593 [0.571, 0.617] | 0.553 [0.528, 0.581] |
| | | paired | +0.263 [+0.239, +0.286] | +0.135 [+0.109, +0.156] | +0.092 [+0.065, +0.115] |
| LightGBM-PCA `seq` | 217 | ESM-2 | 0.731 [0.714, 0.752] | 0.531 [0.506, 0.557] | 0.464 [0.436, 0.495] |
| | | paired | +0.318 [+0.286, +0.349] | +0.119 [+0.090, +0.143] | +0.050 [+0.022, +0.073] |
| LightGBM-PCA `seq_struct` | 213 | SaProt | 0.745 [0.730, 0.763] | 0.582 [0.559, 0.605] | 0.532 [0.503, 0.565] |
| | | paired | +0.287 [+0.263, +0.309] | +0.124 [+0.099, +0.143] | +0.072 [+0.043, +0.096] |
| MLP (MSE) `seq_struct` | 198 | SaProt | 0.762 [0.746, 0.779] | 0.622 [0.604, 0.640] | 0.570 [0.551, 0.592] |
| | | paired | +0.303 [+0.281, +0.323] | +0.163 [+0.147, +0.178] | +0.111 [+0.094, +0.126] |
| MLP (RankNet) `seq_struct` | 198 | SaProt | 0.762 [0.747, 0.779] | 0.613 [0.596, 0.631] | 0.565 [0.546, 0.585] |
| | | paired | +0.303 [+0.282, +0.323] | +0.154 [+0.139, +0.168] | +0.105 [+0.091, +0.118] |

Every paired interval excludes 0 except one-hot ridge's, which excludes it on the other side. The
MLP's 198 assays exclude the 15 smallest, which supervised models do worst on: its numbers are
never set against a 213-assay one (ADR 0023). Per function type (ridge `seq_struct` − SaProt,
contiguous): stability +0.162 (66 assays), organismal fitness +0.126 (74), activity +0.090 (42),
expression +0.071 (18), binding +0.010 [−0.106, +0.121] (13; the only one whose interval includes
0).

**By size** (ridge `seq_struct` − SaProt, mean of per-assay differences; figure
`final_by_size`):

| Variants | Assays | random | modulo | contiguous |
|---|---:|---:|---:|---:|
| < 200 | 8 | +0.116 [−0.050, +0.298] | −0.194 [−0.410, +0.016] | −0.244 [−0.444, −0.046] |
| 200–299 | 7 | +0.143 [−0.009, +0.299] | +0.100 [−0.006, +0.243] | +0.018 [−0.043, +0.079] |
| 300–999 | 42 | +0.258 [+0.216, +0.302] | +0.157 [+0.127, +0.190] | +0.137 [+0.109, +0.168] |
| 1,000–1,999 | 74 | +0.282 [+0.247, +0.317] | +0.177 [+0.147, +0.208] | +0.155 [+0.127, +0.185] |
| 2,000–4,999 | 38 | +0.268 [+0.227, +0.312] | +0.143 [+0.114, +0.177] | +0.096 [+0.064, +0.130] |
| ≥ 5,000 | 44 | +0.295 [+0.238, +0.353] | +0.180 [+0.143, +0.219] | +0.121 [+0.086, +0.158] |

Below 300 variants no interval on unseen positions is above 0; from 300 up every one is. The
eligibility rule was fixed before this table (ADR 0023); the table agrees with it.

### Final models (`train final`, 198 assays, 1,808 s on 13 workers)

- **Bundle:** 1,379,989 substitutions (`data/processed/supervised_landscapes/ridge_seq_struct/`,
  71 MB): 663,263 measured, 131,089 unmeasured at partially measured positions, 585,637 at
  unmeasured positions. 229 measured variants are alone at their position and take the
  contiguous scheme. Stamp: code `6fb0d07`, config `6bd25d40…`, manifest `ca36b024…`.
- **Checks, all passed:** no NaN; no interval with lower ≥ upper; CV+ refit against the saved
  calibration: maximum difference **0** (fold labels identical) in all 198 assays; out-of-fold
  pooled Spearman against `cv_ridge_seq_struct.parquet`: maximum difference 0 (random),
  3.5e-7 (modulo, from the saved rows) and 6.6e-8 (contiguous).
- **Penalties:** median `alpha` 10^1.5 (random fit) and 10^2 (contiguous fit); the contiguous fit
  chose a larger penalty in 62% of assays, the same in 18%, a smaller one in 20%. 10 and 18 fits
  sit at the grid's low end (10^−3), none at the high end (ADR 0021: the low end is a plateau).
- **Interval width** (median over assays of the per-assay median, in assay-SD units): 90% 1.91
  at partially measured positions, 2.70 at unmeasured ones (80%: 1.38 / 2.00); measured variants
  1.90 (random) and 2.61 (contiguous). The unseen-position widths match ADR 0024's 2.61 under
  contiguous.
- **Out-of-fold coverage of measured variants:** 0.800 at 80% and 0.900 at 90% (per assay 0.799–
  0.802 and 0.899–0.903). By construction; it proves nothing (see "Deviations and caveats").
- **Imputed structure features:** 108,452 substitutions, all in ZIKV (55,461 of 65,037) and POLG
  (52,991 of 57,627).
- **Sanity check (reported, not failed):** median prediction lower at buried (relative SASA < 0.2)
  than at exposed residues in **194 of 195** assays that have both. Three have no buried residue:
  KCNE1_HUMAN_Muhammad_2023 (expression and function; a single transmembrane helix, modelled
  alone) and SYUA_HUMAN_Newberry_2020 (α-synuclein, intrinsically disordered). The exception is
  TADBP_HUMAN_Bolognesi_2019 (buried 0.039 vs exposed 0.029): its assay measures the toxicity of
  TDP-43's disordered C-terminal domain, where aggregation-promoting substitutions score
  differently and the buried residues lie in unmeasured folded domains.
- **Published (staged):** model repo `build/hf_model` (9 MB: fits, 1,048,212 coefficients with
  imputation values, manifest, version, configs, results, card); dataset revision `build/hf_dataset`
  with `supervised_landscapes` (61 MB Parquet) and `supervised_landscape_assays`.

## Deviations and caveats

### The by-size figure uses a plain mean, not the ProteinGym aggregate

CLAUDE.md requires ProteinGym-style aggregation (per UniProt ID, per function type, overall) for
every headline. The by-size table and figure (`final_size_comparisons.parquet`,
`final_by_size`) deviate from it on purpose:

- **With the ProteinGym aggregate**, the 7-assay 200–299-variant bin gave ridge − SaProt under
  contiguous **+0.053 [+0.041, +0.066]**, although the 7 per-assay differences span **−0.094 to
  +0.129**. The bin holds 4 organismal-fitness, 2 activity and 1 expression assay. The aggregate
  weights the three function types equally, and its bootstrap resamples assays within each
  function type, so the single expression assay contributes no resampling spread at all and the
  two activity assays very little. The interval was an artefact of the stratification.
- **With a plain mean** of the per-assay paired differences, assays resampled with replacement and
  unstratified (the same draw for both runs, so still paired), the same bin is **+0.018
  [−0.043, +0.079]**, consistent with its spread. ADR 0023's size table used plain means too.
- **The headline numbers remain ProteinGym aggregates**: `final_summary.parquet`,
  `final_comparisons.parquet`, the ladder figure, the model card and every table above except the
  by-size one. The by-size view is a diagnostic of where supervision pays, never a headline, and
  its schema, figure note and code docstring say it is a plain mean.

### The 0.800 / 0.900 coverage of measured variants proves nothing

The out-of-fold intervals of measured variants cover 0.800 at 80% and 0.900 at 90%, on average
and in every assay to within 0.003. That holds **by construction**: each measured variant's
half-width is the CV+ order statistic of the other measured variants' out-of-fold residuals, and
its own residual comes from the same exchangeable set, so it falls inside the interval in almost
exactly the nominal share of rows. It checks that the code computes what it intends; it is not
evidence that the intervals are calibrated.

The evidence is Task 7's held-out test-fold coverage (ADR 0024), where every interval was built
without the test fold's labels: **0.908–0.911 at 90%** (0.815–0.819 at 80%) with scheme-like
calibration, under every split, and no assay more than 10 points below target on the random and
contiguous splits. Calibration on random folds, which the landscapes never use for unseen
positions, covered only 0.806 at 90% under contiguous.

## Alternatives rejected

- **One final fit (contiguous `alpha`) for every variant.** Simpler and one coefficient set, but
  at positions with measurements the interval (random-fold CV+) would be centred on models tuned
  differently from the point prediction, and the penalty would be tuned for a task those variants
  do not pose.
- **Choosing the model per assay** (best of ridge / LightGBM / MLP on its own CV). A selection on
  the test folds; with 198 noisy per-assay comparisons it would mostly pick noise.
- **The final model's prediction at measured variants.** An in-sample fit: it would look better
  than anything the model can do at an unmeasured variant.
- **Plain CV+ intervals at measured variants.** K − 1 of the K models trained on the variant.
- **The ProteinGym aggregate within size bins** (section 1): degenerate intervals for small bins.
- **Imputing structure features with 0 or dropping them for the two partial targets.** 0 is a
  real value (fully buried, no contacts); a second model without structure for those residues
  would be a per-assay model change.
- **Adaptive (normalised) intervals.** They over-cover by 4–5 points at 35–43% more width
  (ADR 0024); the fold-ensemble spread can be shown separately later.

## Consequences

- New commands `train final-table`, `train final-figures`, `train final`, `train publish`, and
  `data publish --landscapes`; config `train.final_table`, `train.final`, `train.publish`. No new
  dependency.
- The Phase 4 export reads the supervised landscapes from `data/processed/supervised_landscapes`
  (or the Hub) and the zero-shot ones from the cache (ADR 0018), choosing per assay from the
  manifest's `default_model`.
- The commands are not Nextflow processes yet, like the other `train` commands.
