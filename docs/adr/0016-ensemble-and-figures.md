# 0016 — Z-score ensemble of ESM-2 650M and SaProt, shared-assay summaries, and figures

- Status: accepted
- Date: 2026-10-13
- Changes: nothing. Uses ADR 0003 (213 assays with a structure) and ADR 0014 (paired comparisons)

## Context

Phase 2 ends with an ensemble of the two 650M models and four figures: model size against
Spearman, bars per function type with CIs, the ensemble's gain, and the wild-type against the
masked marginal. Three things had to be settled:

- **How the members are combined.** The task fixes the rule: z-score within each assay, then
  the mean.
- **Which assays.** SaProt has scores for 213 assays, ESM-2 for 217. Each score set's summary
  file covers its own assays, so the two files cannot be drawn side by side.
- **What a figure may compute.** CLAUDE.md: figures come from `results/*.parquet` only, and a
  difference between two score sets is never judged from two separate CIs.

## Decision

1. **The ensemble is the mean of within-assay z-scores.** For each member and each assay, the
   scores are centred on their mean and divided by their population standard deviation over the
   assay's variants; the ensemble score is the mean of the members' z-scores
   (`scoring/ensemble.py`, `zscore_mean`). Z-scoring is monotone within an assay, so each
   member keeps its own ranking and its own per-assay Spearman; only the combination changes.
   The rule has no fitted parameter and uses no label.
2. **It is built from written scores, not from a model.** `mutascope score ensemble` reads the
   members' score sets under `processed/zero_shot/<label>/` and writes
   `processed/zero_shot/<name>/` in the same schema. It does not implement the `Scorer`
   protocol: a z-score needs every variant of an assay, which a `(L, 20)` landscape of one
   sequence does not have. A landscape-level ensemble for the website is a later decision.
3. **An ensemble is a model config with `members`.** `conf/model/ensemble_esm2_650m_saprot_650m_af2.yaml`
   lists `{model, method}` pairs; each member's label comes from `run_label`. The existing
   `mutascope eval zero-shot` benchmarks it, so there is still one writer of benchmark results
   (ADR 0014), and the results use the same schema and file names as every other score set.
4. **The ensemble exists on the assays every member has.** `_model_assays` treats an ensemble
   as structure-aware if any member is, so it is scored, benchmarked and compared on the 213
   assays. A member may lack only the assays in `data.structure_exceptions`; any other
   difference between members, in assays or in variants, is an error. A member with constant
   scores in an assay is an error too.
5. **Tracked runs name the members.** An ensemble has no checkpoint. Its scoring and benchmark
   runs log `method = zscore_mean` and each member's Hub id and pinned revision.
6. **`mutascope eval shared` writes summaries on shared assays.** For the score sets in
   `eval.shared_summary` it recomputes the ProteinGym aggregate and bootstrap CI on the assays
   all of them have, from the per-assay results files, into
   `results/zero_shot/shared_summary.parquet`. Every score set is resampled from the same seed.
   The per-function-type bars read this file.
7. **Figures read results files and compute nothing.** `eval/figures.py` takes frames and
   returns figures. A missing value, a pair that is not in `comparisons.parquet`, or score sets
   with different assay counts in one figure raise `FigureDataError`. What a figure shows is
   configured in `conf/eval/figures.yaml`.
8. **The two kinds of interval are drawn differently.** A score set's own CI is a grey whisker
   and the note under the figure says it does not test a difference. A paired-bootstrap CI is
   a coloured line around the difference; a filled marker means it excludes zero. The
   model-size figure writes the paired difference of each size step under the step. The
   ensemble gain and the wild-type against masked comparison are drawn as differences only.
9. **matplotlib, as the optional `figures` extra.** The EDA notebook uses Altair, which needs a
   second package (`vl-convert`) to write SVG or PNG. matplotlib writes both, and was already
   installed as a dependency of MLflow. The figures use its bundled DejaVu Sans, carry no
   creation date, and have trailing whitespace removed from the SVG, so redrawing unchanged
   results gives identical files. Colours are the first three slots of the palette in
   `eda/charts.py`, checked together for colour-vision deficiency.
10. **Figures are committed** under `results/figures/` as SVG and PNG, like the results they
    are drawn from.

## Evidence

**Ensemble** (ProteinGym v1.3, 213 assays, 665,793 single mutants, ProteinGym-style aggregate,
95% bootstrap CI; from `results/zero_shot/shared_summary.parquet`):

| Score set | Spearman | 95% CI |
|---|---:|---|
| ESM-2 650M | 0.4144 | 0.3907–0.4431 |
| SaProt 650M | 0.4600 | 0.4417–0.4827 |
| Ensemble | 0.4663 | 0.4467–0.4902 |

The ensemble's other metrics: AUC 0.7616, MCC 0.3657 (212 assays each), NDCG@10% 0.7777,
top-10% recall 0.2137.

**Paired comparisons** (`results/zero_shot/comparisons.parquet`, Spearman, A − B, 95% paired
bootstrap CI, 213 assays):

| A − B | Level | Difference | 95% CI | A better in |
|---|---|---:|---|---:|
| Ensemble − ESM-2 650M | Overall | +0.0519 | +0.0419 to +0.0615 | 100% |
| Ensemble − SaProt | Overall | +0.0063 | −0.0018 to +0.0145 | 93.8% |
| Ensemble − SaProt | Activity (42) | +0.0082 | −0.0030 to +0.0219 | 92.4% |
| Ensemble − SaProt | Binding (13) | +0.0038 | −0.0276 to +0.0313 | 54.5% |
| Ensemble − SaProt | Expression (18) | −0.0100 | −0.0253 to +0.0070 | 17.3% |
| Ensemble − SaProt | OrganismalFitness (74) | +0.0295 | +0.0202 to +0.0403 | 100% |
| Ensemble − SaProt | Stability (66) | −0.0001 | −0.0119 to +0.0112 | 49.7% |

The ensemble is clearly better than ESM-2 650M, overall and in every function type. Against
SaProt, its better member, the overall interval includes zero: **the benchmark does not show
that the ensemble beats SaProt overall.** The one function type with a clear gain is
OrganismalFitness, the type where SaProt has no clear advantage over ESM-2 (+0.0046, ADR
0015). On the other metrics against SaProt: MCC +0.0076 (CI +0.0004 to +0.0141), AUC +0.0049
(−0.0002 to +0.0096), NDCG +0.0002 and top-10% recall +0.0011 (both intervals include zero).
None of these intervals is corrected for the number of comparisons.

Per assay, the ensemble's Spearman is above both members in 97 of 213 assays, above SaProt in
131, above ESM-2 in 177, and below both in 2.

**Why standardise.** The members' score spreads differ by assay: the ratio of SaProt's to
ESM-2's standard deviation within an assay runs from 0.50 to 3.43 (median 1.04). The members
agree with each other with a median per-assay Spearman of 0.80 (lowest 0.16).

**Other combination rules** (one ad hoc run of the same harness on the same 213 assays;
aggregates only, no CI, not written to `results/`): mean of raw scores 0.4735, mean of
within-assay percentile ranks 0.4627, z-score mean 0.4663. The raw mean is higher than the
z-score mean. The z-score rule was fixed before any result was seen and is kept: choosing
among rules by their benchmark score would be tuning on the test data. The author decided
(2026-10-13) not to run a paired comparison of the raw mean either: the z-score rule stands and
the raw mean is not a score set of this project.

**Shared summary.** SaProt's rows in `shared_summary.parquet` are identical to its own summary
file (same assays, same seed). ESM-2 650M is 0.4144 on the 213 shared assays and 0.4125 on all
217.

**Figures.** `model_size_vs_spearman`, `function_type_spearman`, `ensemble_gain` and
`fast_vs_masked`, each as `.svg` and `.png` in `results/figures/`. Drawing them twice gives
byte-identical files (a test checks this).

## Consequences

- `results/` gains `zero_shot_ensemble_esm2_650m_saprot_650m_af2_single{,_summary}.parquet`,
  `zero_shot/shared_summary.parquet` and `figures/`. `comparisons.parquet` has 240 rows (8
  pairs); the first 180 are unchanged.
- A new figure comparison needs its pair in `eval.comparisons` first; the figure command says
  so when it is missing.
- The order is `score ensemble` → `eval zero-shot` → `eval compare` → `eval shared` →
  `eval figures`. These are CLI commands and are not Nextflow processes yet: the test profile
  scores one small model on two assays, which cannot fill a figure with five function types.
  Wiring them is left for the pipeline work of Phase 4.
- The ensemble is defined per assay. It cannot score a new sequence with no assay; the API's
  live scoring is not affected.
- `F7YBW8_MESOW_Aakre_2015` has undefined AUC and MCC for the ensemble, as for every model
  (all its single mutants have label 1).
