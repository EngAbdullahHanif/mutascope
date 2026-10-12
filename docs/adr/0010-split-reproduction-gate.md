# 0010 — Split the reproduction gate into a harness gate and a model gate

- Status: accepted (evaluation-protocol change approved by the author, 2026-10-12)
- Date: 2026-10-12

## Context

The reproduction gate in CLAUDE.md said that our ESM-2 650M zero-shot average Spearman must
match ProteinGym's published value (0.414, `Summary_performance_DMS_substitutions_Spearman.csv`
at the pinned commit `1f8de97`) within ±0.01. That compares two different things.
ProteinGym's leaderboard scores **every** row of the 217 substitution assays: 2,465,767 variants,
of which 1,769,456 (72%, in 69 assays) are multi-mutants. MutaScope v1 keeps single mutants only
(696,311 rows; ADR 0005), so our model never produces the scores the published number is based on.

The difference matters per assay, even though it nearly vanishes in the headline. We ran a
scratch check on ProteinGym's own precomputed `ESM2_650M` scores, aggregated per UniProt ID,
then per function type, then overall:

| Rows | Assays | Aggregate Spearman |
|---|---:|---:|
| All rows (singles + multis) | 217 | 0.4137 (published: 0.414) |
| Single mutants only | 217 | 0.4125 |

Per assay, the all-row Spearmans match ProteinGym's DMS-level file to within 0.0005, which is
its 3-decimal rounding. Dropping multi-mutants changes the Spearman of all 69 multi-mutant assays.
It rises in 26 and falls in 43, and 28 move by more than 0.1. The extremes are
`SPG1_STRSG_Wu_2016` (0.128 → 0.551) and `HIS7_YEAST_Pokusaeva_2019` (0.411 → 0.028). The
overall number moves by only 0.0012 because these shifts cancel out. A single-mutant score checked
against 0.414 would therefore pass or fail by coincidence, so the old gate could not tell a
correct model from a broken one. It also mixed two possible bug sources, the metrics and
aggregation code and the scoring model, into one number.

## Decision

Replace the single gate with two gates. Both must pass before any new modelling work.

1. **Harness gate** (fast, no model). Our metrics and aggregation code, applied to ProteinGym's
   precomputed `ESM2_650M` scores over **all** rows, must reproduce the published 0.414:
   |ours − 0.414| ≤ 0.0005, i.e. it rounds to the published value. This tests only our harness,
   against exactly the data the leaderboard used. ProteinGym's precomputed scores are already
   downloaded and checksummed (`zero_shot_scores` in `conf/data/proteingym.yaml`), so this gate
   needs no model or GPU. It does need the downloaded data, so it is skipped where the data is
   absent, as in CI.
2. **Model gate** (`slow`; runs our ESM-2 650M, on MPS locally). On the 696,311 single mutants:
   - **Per-variant agreement.** In every assay, the Spearman between our ESM-2 650M scores and
     ProteinGym's precomputed `ESM2_650M` scores on the same variants is ≥ 0.99. This catches
     model bugs, such as a wrong masking scheme, token offset or window choice, that an
     aggregate could hide.
   - **Aggregate agreement.** Our single-mutant aggregate Spearman against `DMS_score` is within
     ±0.01 of the aggregate the same harness computes from ProteinGym's precomputed scores on the
     same rows. That reference is computed at test time, not hard-coded. It is about 0.4125 today.

The harness gate makes the published number trustworthy as a check of our code, so the model
gate can use our harness on their scores as its reference.

## Consequences

- `tests/test_reproduction.py` holds two tests: the harness gate and the model gate (`slow`).
  The ±0.01 aggregate tolerance is unchanged; it now applies to a like-for-like comparison.
- The harness needs the multi-mutant rows of ProteinGym's precomputed score files. It reads
  them from the raw zero-shot Parquet, not from the processed (single-only) `variants` table.
  This is the only place multi-mutants are used, and only for this check.
- "Every assay ≥ 0.99" is strict on purpose. The likely failures are the 16 targets longer than
  1,022 residues, where our window choice may differ from ProteinGym's. A failure there means
  reading their windowing code and matching it, not relaxing the threshold. Any change to the
  threshold or tolerances needs the author's approval and a new ADR.
- Reported results remain single-mutant results. They are never presented as leaderboard
  numbers and never compared with the published 0.414. The benchmark page labels them
  "ProteinGym v1.3, single mutants".
