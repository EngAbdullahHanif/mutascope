# 0017 — Phase 2 close: zero-shot model zoo and benchmark harness

- Status: accepted
- Date: 2026-10-13
- Changes: nothing. Carries one open Phase 2 item into the later phases (see "Open and weak")

## Context

Phase 2 (weeks 3–4) had one goal: several pretrained models score every measured mutation, and
our numbers reproduce the ProteinGym leaderboard. Its done-when line has three parts: full
landscapes for all assays from ESM-2 (three sizes) and SaProt, harness and model gates passing,
and figures generated. The gates pass and the figures exist. The first part is met for the
positions the assays measure, which is all the benchmark needs, and **not** for full landscapes
at every position. That item stays open and is listed below with its size. This record
summarises what exists, what is weak, and what Phase 3 builds on. Details are in ADRs
0010–0016 and build-log entries 9–16.

## What Phase 2 produced

**Headline table.** Aggregate Spearman on single mutants, ProteinGym-style (per UniProt ID, then
per function type, then overall), with 95% bootstrap CIs over assays (10,000 resamples, seed 0,
stratified by function type). The assay column gives the total and, in brackets, the count per
function type in the order of the columns. Each row is read from that score set's
`results/zero_shot_<label>_single_summary.parquet`; the "shared assays" row is from
`results/zero_shot/shared_summary.parquet`.

| Score set | Assays | Overall | Activity | Binding | Expression | OrganismalFitness | Stability |
|---|---:|---|---|---|---|---|---|
| ESM-2 35M | 217 (43/13/18/77/66) | 0.3113 [0.2848, 0.3376] | 0.3060 [0.2586, 0.3576] | 0.3158 [0.2174, 0.4117] | 0.3368 [0.2807, 0.3932] | 0.2114 [0.1751, 0.2458] | 0.3865 [0.3526, 0.4209] |
| ESM-2 150M | 217 (43/13/18/77/66) | 0.3820 [0.3576, 0.4081] | 0.3873 [0.3457, 0.4367] | 0.3511 [0.2559, 0.4455] | 0.3968 [0.3443, 0.4536] | 0.2941 [0.2601, 0.3358] | 0.4805 [0.4503, 0.5106] |
| ESM-2 650M | 217 (43/13/18/77/66) | 0.4125 [0.3893, 0.4408] | 0.4203 [0.3787, 0.4730] | 0.3790 [0.2874, 0.4785] | 0.4089 [0.3508, 0.4738] | 0.3582 [0.3264, 0.4014] | 0.4962 [0.4698, 0.5226] |
| ESM-2 650M, shared assays | 213 (42/13/18/74/66) | 0.4144 [0.3907, 0.4431] | 0.4264 [0.3852, 0.4782] | 0.3790 [0.2869, 0.4768] | 0.4089 [0.3496, 0.4746] | 0.3613 [0.3284, 0.4052] | 0.4962 [0.4698, 0.5221] |
| SaProt 650M (AF2) | 213 (42/13/18/74/66) | 0.4600 [0.4417, 0.4827] | 0.4583 [0.4229, 0.5007] | 0.4292 [0.3671, 0.5008] | 0.4816 [0.4309, 0.5362] | 0.3660 [0.3346, 0.4029] | 0.5647 [0.5420, 0.5882] |
| Ensemble (ESM-2 650M + SaProt) | 213 (42/13/18/74/66) | 0.4663 [0.4467, 0.4902] | 0.4666 [0.4300, 0.5120] | 0.4330 [0.3614, 0.5078] | 0.4716 [0.4177, 0.5297] | 0.3955 [0.3651, 0.4341] | 0.5646 [0.5438, 0.5857] |
| ESM-2 35M, wild-type marginal | 217 (43/13/18/77/66) | 0.3028 [0.2768, 0.3290] | 0.2952 [0.2477, 0.3459] | 0.3115 [0.2156, 0.4085] | 0.3272 [0.2720, 0.3826] | 0.2022 [0.1655, 0.2364] | 0.3782 [0.3465, 0.4107] |
| ESM-2 150M, wild-type marginal | 217 (43/13/18/77/66) | 0.3772 [0.3537, 0.4028] | 0.3805 [0.3393, 0.4294] | 0.3498 [0.2595, 0.4425] | 0.3952 [0.3437, 0.4512] | 0.2878 [0.2535, 0.3287] | 0.4727 [0.4432, 0.5022] |
| ESM-2 650M, wild-type marginal | 217 (43/13/18/77/66) | 0.4078 [0.3855, 0.4357] | 0.4139 [0.3728, 0.4665] | 0.3738 [0.2877, 0.4714] | 0.4053 [0.3488, 0.4674] | 0.3486 [0.3173, 0.3912] | 0.4974 [0.4725, 0.5220] |

How to read it:

- The 213-assay rows leave out the four assays without a usable structure (ADR 0003): one
  Activity assay (CAS9) and three OrganismalFitness assays (P53). A 217-assay row and a
  213-assay row are not on the same assays. To set ESM-2 650M beside SaProt or the ensemble,
  use its "shared assays" row. ESM-2 35M, ESM-2 150M and the wild-type marginals have no
  213-assay summary.
- A row's CI describes that score set alone. Two rows are compared only by the paired
  bootstrap below, never by looking at whether their CIs overlap (ADR 0014).
- These are single-mutant aggregates. None of them is comparable with the published
  leaderboard values (0.414 for ESM-2 650M, 0.457 for SaProt 650M), which include
  multi-mutants.
- Zero-shot predictions of functional effect in DMS assays. Nothing here is supervised or
  clinical.

**Paired comparisons** (overall Spearman, A − B, `results/zero_shot/comparisons.parquet`; not
corrected for the number of comparisons):

| A − B | Assays | Difference [95% CI] | Clear? |
|---|---:|---|---|
| ESM-2 150M − ESM-2 35M | 217 | +0.0707 [+0.0589, +0.0840] | yes |
| ESM-2 650M − ESM-2 150M | 217 | +0.0306 [+0.0225, +0.0405] | yes |
| ESM-2 35M: masked − wild-type | 217 | +0.0085 [+0.0061, +0.0107] | yes |
| ESM-2 150M: masked − wild-type | 217 | +0.0048 [+0.0022, +0.0070] | yes |
| ESM-2 650M: masked − wild-type | 217 | +0.0047 [+0.0007, +0.0079] | yes, barely |
| SaProt − ESM-2 650M | 213 | +0.0456 [+0.0283, +0.0625] | yes |
| Ensemble − ESM-2 650M | 213 | +0.0519 [+0.0419, +0.0615] | yes |
| Ensemble − SaProt | 213 | +0.0063 [−0.0018, +0.0145] | no, includes zero |

**Reproduction gate.**

| Gate | Result |
|---|---|
| Harness (all rows, ProteinGym's `ESM2_650M` scores) | 0.4137, rounds to the published 0.414 (Δ 0.0003) |
| Model, ESM-2 35M (217 assays, 696,311 variants) | lowest per-variant Spearman 0.99999996; aggregate 0.3113, equal to theirs |
| Model, ESM-2 150M (217, 696,311) | lowest 0.99999991; aggregate 0.3820, equal to theirs |
| Model, ESM-2 650M (217, 696,311) | lowest 0.99999996; aggregate 0.4125, equal to theirs |
| Model, SaProt 650M (211 gated of 213, 663,898) | lowest 0.999695; aggregate 0.4595 for both |
| SaProt gate exceptions (2, each with a replay test) | `POLG_HCVJF_Qi_2014` 0.2770, `BRCA2_HUMAN_Erwood_2022_HEK293T` −0.0611 against ProteinGym's scores |

The fast suite and the slow suite (model gates and replays) were both rerun on the closing
commit; the counts and times are in build-log entry 17.

**Model time (M4 Pro, MPS, from the cache log).** Masked marginal at the 37,753 measured
positions: 35M 1,119 s, 150M 1,718 s, 650M 6,081 s. Wild-type marginal, all 62,671 residues:
7.7 s, 8.7 s and 24.5 s. SaProt, 35,992 positions: 6,289 s.

**In git.** The scoring package (`Scorer` protocol, registry, cache, windows, ESM-2, SaProt,
structure tokens, ensemble), the evaluation package (metrics, aggregation, bootstrap, paired
comparison, gate, figures), Hydra groups `model`, `scoring`, `eval`, `tracking`, the Nextflow
processes `SCORE_ZERO_SHOT` and `BENCHMARK_ZERO_SHOT`, all files under `results/` (per-assay
and summary Parquet for 8 score sets, 4 gate tables, comparisons, shared summary, 4 figures as
SVG and PNG), ADRs 0010–0017 and the draft upstream report. Not in git: the log-probability
cache (37 MB; 1,307 benchmark entries and 4 labelled replay entries), the score sets under `data/processed/zero_shot/` and the
MLflow store (32 runs).

## Open and weak

Unchecked roadmap items:

1. **Full masked landscapes at every position.** The cache holds the measured positions only.
   Missing: 24,918 of 62,671 residues for each ESM-2 size (187 entries), 28,886 of 64,878
   residues for SaProt (185 entries), and the full targets of the five MSA-region assays, which
   have no cache entry at all. The roadmap named only the ESM-2 figure; it now names both.
   Nothing in the benchmark depends on this. The Phase 4 export of precomputed landscapes does,
   so the item moves there as a prerequisite and stays unticked.
2. **ESM-1v ensemble and ESM C 300M** (stretch). Not started; ESM C's licence is not checked.
3. **`docker` pipeline profile** (Phase 1 item). Waits for the Phase 4 image.

Weak points in what is ticked:

4. **`make pipeline` does not reproduce the whole benchmark.** Scoring and per-model benchmark
   are Nextflow processes. `score ensemble`, the ensemble's `eval zero-shot`, `eval compare`,
   `eval shared` and `eval figures` are CLI commands run by hand. The committed comparisons, shared summary and figures come from those hand runs.
5. **CI enforces neither gate.** The harness gate needs the 1.9 GB score archive and is skipped
   in CI. The model gates are `slow` and need MPS and the cache. Both run only on the dev
   machine, so a regression is caught only if someone runs them.
6. **The ensemble is not shown to beat SaProt.** Overall +0.0063 with a CI that includes zero;
   clear only for OrganismalFitness. It is kept as specified (ADR 0016).
7. **Binding rests on 13 assays.** Its CIs are 0.13 to 0.19 wide in every row, and only two of
   the eight paired differences are clear for it (150M over 35M, the ensemble over ESM-2
   650M).
8. **Coverage is uneven across score sets.** Only three score sets have a 213-assay summary.
   SaProt has no wild-type-marginal score set, by design (ADR 0015).
9. **The upstream report is a draft.** `docs/upstream/proteingym_saprot_issue.md` describes the
   two wrong SaProt inputs and is not posted.
10. **`results/data/zero_shot_run_esm2_35m.parquet` shows one invocation of three** (19,388
    positions, 541 s). The full figure (37,753 positions, 1,119 s) is in the cache log and in
    ADR 0014.

None of these changes a number in the table.

## Decisions that mattered most

1. **A gate in two parts, each testing one thing (ADR 0010).** The published 0.414 includes
   multi-mutants, so it can check the harness but not a single-mutant model run. The harness
   gate reproduces 0.414 from ProteinGym's own scores. The model gate compares our scores with
   theirs variant by variant. With both passing, a number in the table differs from ProteinGym's
   only where the inputs differ, and that is how the two wrong upstream SaProt inputs were found
   and proven by replay instead of being tuned away (ADR 0015).
2. **Score only what is measured, and make the cache the checkpoint (ADRs 0012, 0013).** The
   benchmark needs 37,753 of 62,671 positions. A cache entry is always `(L, 20)` with NaN for
   unscored rows, lives outside every output directory, and is keyed by model, revision,
   sequence, structure tokens, method and region. A killed run resumes, the gate and the
   ensemble cost no model time, and full landscapes later fill the same entries.
3. **Differences are paired, and rules are fixed before the result (ADRs 0014, 0016).** Every
   comparison is a paired bootstrap on shared assays. The ensemble's z-score rule was kept when
   a raw mean scored higher (0.4735 against 0.4663 in a side check), because choosing the rule
   on the benchmark would be tuning on the test data.

## What Phase 3 inherits

- **Zero-shot score features:** 8 score sets under `data/processed/zero_shot/<label>/`, named
  by `run_label`. Six cover 217 assays (696,311 variants). SaProt and the ensemble cover 213
  (665,793).
- **20-dimensional log-probabilities at position i:** already in the cache for every measured
  position, for ESM-2 (three sizes, benchmark scoring region) and SaProt. No model run needed.
- **Embeddings:** not cached. Position-i embeddings of ESM-2 650M and SaProt need new model
  passes and their own cache.
- **Fold assignments:** downloaded and checksummed (`cv_folds_singles_substitutions`, 217
  files with `fold_random_5`, `fold_modulo_5`, `fold_contiguous_5`), not yet in the processed
  variants table. Joining them is the first Phase 3 data step.
- **Decisions Phase 3 must make and record:**
  - The four assays without a structure have no SaProt, ensemble or structure features. Either
    the models using those features are reported on 213 assays, or those features are absent
    for the four. State the count either way.
  - The ensemble score is a within-assay z-score over all of an assay's variants, so its mean
    and standard deviation include test-fold rows. It uses no labels, but under the rule that
    scalers are fit on training folds only it should be rebuilt per fold, or left out in favour
    of the two member scores.
  - The supervised table compares against zero-shot Spearman computed per test fold, not
    against the whole-assay values above.
- **Harness:** metrics, ProteinGym aggregation, bootstrap CIs, `paired_bootstrap_diff`, the
  long results format with `split` and `fold` columns (empty for zero-shot), and MLflow
  tracking with the rule that tracked metrics are copied from results files.
- **Known data issues:** `F7YBW8_MESOW_Aakre_2015` has one class only, so AUC and MCC cover 216
  assays (212 of the 213). ProteinGym's binary cutoffs include multi-mutants, which skews
  single-mutant labels towards 1 in 32 assays (ADR 0009).
