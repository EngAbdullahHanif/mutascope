# MutaScope ridge `seq_struct`: per-assay mutation-effect models for ProteinGym v1.3

One ridge regression per deep mutational scanning (DMS) assay, trained on that assay's measured
single substitutions, that predicts the effect of **every** single amino-acid substitution of the
protein (all 19 × L), with 80% and 90% prediction intervals. These are the supervised models
behind [MutaScope](https://github.com/EngAbdullahHanif/mutascope); the predicted landscapes are
in the dataset
[engAbdullahHanif/mutascope-proteingym-v1.3](https://huggingface.co/datasets/engAbdullahHanif/mutascope-proteingym-v1.3)
(configs `supervised_landscapes` and `supervised_landscape_assays`).

Predictions are **functional effects in laboratory DMS assays** (growth, binding, stability,
activity, expression), on each assay's own scale. They are **not clinical pathogenicity
predictions and must not be used for clinical or diagnostic decisions.**

## Model

- **Type:** ridge regression, one per assay, 198 assays. The same model and features everywhere;
  nothing is selected per assay.
- **Features (`seq_struct`, 2,647 columns per variant):** wild-type and mutant one-hot (40),
  BLOSUM62 (1), relative position (1); ESM-2 650M masked-marginal score (1), its 20
  log-probabilities at the position and its 1,280-dimensional embedding of the residue; the same
  three blocks from SaProt 650M (AF2 structure tokens); pLDDT, relative SASA and contact count
  from the AlphaFold2 structure (3). The zero-shot models are pinned by revision in the configs.
- **Penalty:** chosen by cross-validation inside the assay's measured variants, over
  ProteinGym's folds. Each assay has **two fits**: `random` (penalty chosen on random folds) and
  `contiguous` (on contiguous position blocks). The `random` fit predicts variants at positions
  that have measurements, the `contiguous` fit variants at positions with none: each is tuned for
  the task it does.
- **Files:** `model/fits.parquet` (per assay and fit: `alpha`, `intercept`, `target_mean`,
  `target_sd`, `n_train`), `model/coefficients.parquet` (per assay, fit and feature:
  `coefficient` and `impute`), `model/manifest.parquet` (every ProteinGym assay: eligible or
  why not), `model/version.json`, `config/` (the configs as committed and resolved), `results/`
  (the evaluation tables).

A prediction is `intercept + Σ coefficient × feature`, in `DMS_score` units, on raw feature values
(a missing structure feature takes its `impute` value):

```python
import pandas as pd

fits = pd.read_parquet("model/fits.parquet").set_index(["assay_id", "fit"])
coef = pd.read_parquet("model/coefficients.parquet")
rows = coef[(coef.assay_id == assay_id) & (coef.fit == "contiguous")]
x = features[rows.feature].fillna(dict(zip(rows.feature, rows.impute)))  # one row per variant
prediction = fits.loc[(assay_id, "contiguous"), "intercept"] + x.to_numpy() @ rows.coefficient.to_numpy()
```

The features come from MutaScope's feature store (`mutascope features build`); the landscapes in
the dataset already hold every prediction and interval.

## Eligibility

A supervised landscape exists for an assay with **a structure and at least 300 measured single
mutants**: 198 of ProteinGym's 217 assays. The rule was set before the final run, from the ridge
and LightGBM results: below 300 variants supervised models lose to zero-shot SaProt on unseen
positions (ridge − SaProt −0.20 under the contiguous split at ≤ 200 variants, ADR 0023). For the other 19
the manifest gives the reason and the model MutaScope shows instead:

| Reason | Assays | Shown instead |
|---|---:|---|
| No structure (AF2 model does not match the target; ProteinGym data issue) | 4 | ESM-2 650M zero-shot |
| Fewer than 300 measured variants (33 to 298) | 15 | SaProt 650M zero-shot |

## Training data

ProteinGym v1.3 single-substitution DMS assays (Notin et al., NeurIPS 2023), processed and
validated as the dataset above: 663,263 measured variants across the 198 eligible assays (326 to
16,897 per assay). Targets are each assay's `DMS_score`, standardised within the assay (higher =
fitter).

## Evaluation

Under ProteinGym's supervised protocol: per assay, 5-fold cross-validation with ProteinGym's
`random`, `modulo` and `contiguous` fold assignments; Spearman over the pooled out-of-fold
predictions; aggregated per UniProt ID, then per function type, then overall. Brackets: 95%
bootstrap CI over assays. **Paired** differences resample the same assays for both models.

| Split | Assays | Ridge `seq_struct` | − zero-shot SaProt 650M, paired |
|---|---:|---:|---:|
| random (positions seen in training) | 213 | 0.721 [0.705, 0.741] | +0.263 [+0.239, +0.286] |
| modulo (every 5th position held out) | 213 | 0.593 [0.571, 0.617] | +0.135 [+0.109, +0.156] |
| contiguous (blocks of positions held out) | 213 | 0.553 [0.528, 0.581] | +0.092 [+0.065, +0.115] |

Most of a landscape is positions with no measurement, which the modulo and contiguous splits
test; the random split only describes positions with measurements. Ridge was chosen as the
deployed model on those two splits: LightGBM and an MLP fit seen positions better (+0.013 to
+0.026 under random) but extrapolate no better (LightGBM-PCA −0.011 / −0.020, MLP-MSE +0.001 /
−0.011 under modulo / contiguous, paired, ADRs 0022–0023).

By function type (contiguous split, ridge − SaProt, paired): stability +0.162, organismal
fitness +0.126, activity +0.090, expression +0.071, binding +0.010 [−0.106, +0.121] (13 assays;
not distinguishable from zero).

## Prediction intervals

Constant-width **CV+** intervals (Barber et al., 2021) at 80% and 90%, calibrated on the folds that
match the prediction: random folds for a variant at a position with measurements, contiguous
folds for a variant at a position without. Coverage was measured on held-out test folds before
deployment (ridge `seq_struct`, 213 assays, mean over assays):

| Situation | Calibration | 80% | 90% | Assays > 10 points below 90% |
|---|---|---:|---:|---:|
| Seen positions (random split) | random folds | 0.818 | 0.911 | 0 |
| Unseen positions (contiguous split) | contiguous folds | 0.815 | 0.908 | 0 |
| Unseen positions, *if calibrated on random folds* | random folds | 0.673 | 0.806 | 86 |

The last row is why calibration follows the position. Honest intervals at unmeasured positions
are wide: median 90% width 2.70 assay standard deviations (1.91 at positions with measurements).

For a **measured** variant the landscape shows its **out-of-fold** prediction (from the fold
model that never saw it), never the final model's in-sample fit, and an interval built from the
other variants' out-of-fold residuals.

## Limitations

- **Not for clinical use.** DMS assays measure a laboratory phenotype in a chosen system; a
  substitution's assay score is not a statement about disease.
- **One assay, one model.** Scores are on each assay's own scale and are not comparable across
  assays, or across assays of the same protein.
- **Extrapolation is the hard case.** At positions far from any measurement, accuracy is near the
  contiguous-split value (aggregate Spearman 0.553) and the intervals are wide.
- **Small and binding assays.** Below 300 variants no supervised landscape is made; for binding
  assays the gain over zero-shot on unseen positions is not distinguishable from zero.
- **Structures are AlphaFold2 predictions.** For two targets most residues lie outside the
  modelled structure (`A0A140D2T1_ZIKV_Sourisseau_2019`: 55,461 of 65,037 landscape variants;
  `POLG_HCVJF_Qi_2014`: 52,991 of 57,627). There the structure features are imputed with their
  measured mean (flag `structure_imputed`) and predictions rely on the sequence and SaProt
  features.
- **Selection of proteins.** ProteinGym reflects which proteins DMS studies chose; 64 of 66
  stability assays come from one study of small domains.
- **Coverage is an average.** CV+ intervals hold their level on average over variants, not for
  every variant or position.

## Version

`model/version.json` and every landscape row carry the git commit of the code, the sha256 of the
resolved config and the sha256 of MutaScope's `data/manifest.yaml` (which pins every input
file's checksum).

## Citation

ProteinGym (Notin et al., 2023), ESM-2 (Lin et al., *Science* 2023), SaProt (Su et al., ICLR
2024), Foldseek (van Kempen et al., *Nat. Biotechnol.* 2024), CV+ (Barber, Candès, Ramdas &
Tibshirani, *Ann. Statist.* 2021). Licence: MIT.
