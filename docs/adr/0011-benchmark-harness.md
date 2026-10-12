# 0011 — Benchmark harness: ProteinGym's metrics, NaN for undefined values, stratified bootstrap

- Status: accepted (decisions 2 and 4 confirmed by the author, 2026-10-12)
- Date: 2026-10-12

## Context

Phase 2 needs a harness that scores any model's predictions against the DMS assays the way
ProteinGym does, so that our numbers can be checked against the leaderboard (ADR 0010's harness
gate) and later reported for our own models. "The way ProteinGym does" is decided by details
that a paper description leaves out. We read `proteingym/performance_DMS_benchmarks.py` at the
pinned commit `1f8de97` and matched it line by line. The details that change numbers:

- **Binarisation.** Labels are the assay file's `DMS_score_bin` (ProteinGym's own cutoff per
  assay). For MCC the *model* is binarised at its own median: `1 if score >= median else 0`.
- **NDCG.** Gains are the continuous `DMS_score`, min-max scaled to [0, 1], not the binary label.
  k = floor(10% of the variants). Ranks come from `argsort(argsort(-score)) + 1`, so ties are
  broken by numpy's default sort.
- **Top-10% recall.** A variant is "top" if it is `>=` the 90th percentile (numpy's linear
  interpolation), for the true and the model scores separately.
- **Aggregation.** The mean per `(UniProt_ID, coarse_selection_type)`, then per
  `coarse_selection_type`, then over the function types. pandas skips NaN at every level.
- **Rounding.** Per-assay values are rounded to 3 decimals *before* aggregating, and the
  aggregates are rounded again.
- **Bootstrap.** The leaderboard's `Bootstrap_standard_error` is the standard error of each
  model's *difference from the top model*, from resampling (UniProt, function) units within
  each function type. It is not a confidence interval of the model's own score.
- **Undefined values.** AUC with one label class raises inside sklearn and ProteinGym records
  NaN. MCC with one class does not raise: sklearn returns 0 and ProteinGym records 0. NDCG
  returns 0 when the top 10% is empty (fewer than 10 variants).

## Decision

1. **Same definitions, same library calls.** `mutascope.eval.metrics` uses scipy `spearmanr`
   and sklearn `roc_auc_score` / `matthews_corrcoef` as upstream does, and ports NDCG and top
   recall. The tests run ProteinGym's own `calc_ndcg`, `calc_toprecall` and group-by chain,
   copied verbatim into `tests/eval/proteingym_reference.py` (MIT), as oracles on random inputs
   with many ties.
2. **Undefined is NaN, never 0.** MCC with a single label or prediction class, and NDCG with
   k = 0, are NaN (upstream: 0). Where upstream already gives NaN, so do we. NaN values are left
   out of every mean, as pandas does upstream, and each run logs a count per model and metric
   with the assay ids. Malformed inputs (NaN scores, length mismatch, labels not 0/1) raise
   instead.

   **This is a deliberate deviation from ProteinGym for MCC, not a bug.** On single mutants the
   only undefined values are AUC and MCC of `F7YBW8_MESOW_Aakre_2015`, whose 37 single mutants
   all have `DMS_score_bin = 1` (on all 9,192 rows half are 1, so the gate never meets it). AUC
   is NaN in both harnesses. MCC differs, with two consequences, measured on ProteinGym's
   precomputed scores for the 696,311 single mutants:
   - *The assay and its protein.* `F7YBW8_MESOW_Aakre_2015`'s MCC is NaN, where ProteinGym
     records 0. Its protein, `F7YBW8_MESOW` (OrganismalFitness, shared with
     `F7YBW8_MESOW_Ding_2023`), therefore has MCC 0.1601, Ding's value alone. With
     ProteinGym's 0 it would be 0.0801: the mean of 0.1601 and a 0 that measures nothing.
     The same holds for both models.
   - *The headline.* ESM-2 650M's overall single-mutant MCC is 0.3253, where ProteinGym's
     convention gives 0.3251 (OrganismalFitness: 0.2820 vs 0.2808). For SaProt 650M it is 0.3547
     vs 0.3544.

   Small, but a bias rather than noise: a 0 can only ever pull an average down.
3. **No intermediate rounding.** We aggregate unrounded per-assay values. The published values
   therefore differ from ours by rounding only, but by up to two roundings: SaProt 650M's Binding
   Spearman is 0.37853 from unrounded values and exactly 0.3785 from the 3-decimal ones, which
   pandas rounds (half to even) to the published 0.378. **The function-level tolerance of 0.001
   exists only because ProteinGym rounds per-assay values to 3 decimals before averaging and
   then rounds the average again; the overall gate keeps ±0.0005 (ADR 0010).** Over all five
   metrics and both models, 3 of the 50 function-level values land beyond 0.0005 for this
   reason: SaProt Binding Spearman (+0.00053), ESM-2 Activity MCC (−0.00056) and ESM-2 Binding
   top-10% recall (+0.00056). All 10 overall values are within 0.00044. A separate test feeds
   ProteinGym's own rounded per-assay values to our aggregation and requires every published
   aggregate exactly.
4. **95% CIs: percentile bootstrap over assays, stratified by function type.** 10,000 resamples
   (seed 0, `conf/eval/proteingym.yaml`). The unit is the assay, as CLAUDE.md specifies;
   matching ProteinGym's protocol is preferred over wider protein-level intervals (see
   Alternatives). Each resample draws, within every function type, as many assays as it has,
   with replacement, and reruns the full hierarchical aggregation.
   Stratifying keeps all five function types in every resample, so the overall value always
   averages the same five categories. The resample is a weight vector, so the point estimate
   (all weights 1) and the bootstrap share one implementation and cannot disagree. These CIs
   are not comparable with the leaderboard's standard errors, which measure something else.
5. **Results format.** Per-assay results are long: `model, assay_id, split, fold, metric,
   value`. `split` and `fold` are null for zero-shot. Aggregates go to a `_summary` file
   (`model, split, metric, level, group, value, ci_low, ci_high, n_assays`). Both have pandera
   schemas. ProteinGym's precomputed scores are named `proteingym:<column>`, so they can never be
   mistaken for our own ESM-2 or SaProt scores.

## Evidence

Our harness on ProteinGym's precomputed scores, all 2,465,767 rows of the 217 assays, against
the leaderboard at the pinned commit. Per-assay values agree within 0.0005 (the published
rounding) for all 217 assays, in every metric, for both models. All ten leaderboard files
(summary and DMS-level, five metrics) are in the pinned, checksummed download, and
`tests/test_reproduction.py` checks every cell of this table.

| Metric | ESM2 (650M) ours / published | SaProt (650M) ours / published |
|---|---|---|
| Spearman | 0.4137 / 0.414 | 0.4566 / 0.457 |
| AUC | 0.7286 / 0.729 | 0.7509 / 0.751 |
| MCC | 0.3269 / 0.327 | 0.3586 / 0.359 |
| NDCG@10% | 0.7472 / 0.747 | 0.7682 / 0.768 |
| Top-10% recall | 0.2163 / 0.216 | 0.2322 / 0.232 |

## Alternatives considered

- **Return 0 like ProteinGym** for full fidelity. Rejected: an undefined value is not a bad
  value, and CLAUDE.md requires NaN. The bias is measured above and the gate is unaffected
  (no metric is undefined on all rows).
- **Resample UniProt IDs instead of assays.** Assays of the same protein are correlated (P53
  has four), so resampling proteins would give somewhat wider, arguably more honest intervals.
  CLAUDE.md specifies a bootstrap over assays, and the author confirmed it (2026-10-12):
  matching ProteinGym's protocol matters more than wider intervals.
- **Paired bootstrap of differences between models** (what the leaderboard's standard error
  measures). Needed when comparing models in Phase 3. The weighted implementation supports it,
  because the same weight matrix can be applied to two models' values.

## Consequences

- `mutascope eval precomputed` writes `results/proteingym_precomputed_{all,single}.parquet`
  and their `_summary` files. `mutascope eval compare-leaderboard` prints the comparison.
- The harness gate in `tests/test_reproduction.py` covers all five metrics for both models
  (15 checks: gate, per-assay and exact-aggregation diagnostics per metric). It runs in about
  4 s with the release present and is skipped without it (CI).
- Our zero-shot scorers will reuse `assay_metrics_table` and `summarise` unchanged; supervised
  results must average folds before `summarise`, which refuses duplicate rows.
