# 0015 — SaProt 650M scorer, its scoring region, and model gate exceptions

- Status: accepted
- Date: 2026-10-12
- Changes: ADR 0012 (the MSA-region rule and the windowing rule now apply to ESM-2 only);
  ADR 0010 (the model gate can have documented exceptions)

## Context

Phase 2 needs a structure-aware model next to ESM-2. SaProt (Su et al., ICLR 2024) is the ESM-2
architecture with a vocabulary of combined tokens: an amino acid plus the Foldseek 3Di state of
that residue. Phase 1 already wrote the 3Di states (`processed/three_di`) and the pLDDT
(`processed/structure_features`) for the 213 assays whose structure matches the target
(ADR 0003).

Two upstream sources define the scoring:

- the official repository, https://github.com/westlake-repl/SaProt, `predict_mut` in
  `model/saprot/saprot_foldseek_mutation_model.py` (commit `e91e4858b55944523f1f8d385f7b96a0d3d34c1d`);
- ProteinGym's baseline, `proteingym/baselines/saprot/compute_fitness.py` at the pinned commit
  `1f8de97`, which contains the same function and produced the `SaProt_650M_AF2` scores the
  model gate compares with.

## Decision

1. **The scoring is a port of `predict_mut`.** A residue is masked by replacing its token with
   `#` + its own 3Di letter (the amino acid is hidden, the structure stays). After a softmax
   over the 446-token vocabulary, the probability of an amino acid is the sum over its 21
   tokens (20 3Di states and `#`). The score is `log P(mt) - log P(wt)`. We store `log P` per
   amino acid, so the cache and the landscape code are the same as for ESM-2. Upstream runs one
   pass per variant; we run one per position and read all 20 substitutions from it, which is
   the same computation for single mutants.

2. **Structure tokens come from the processed tables, never from Foldseek at scoring time.**
   `scoring.struct_tokens` builds one character per target residue: the lowercase 3Di letter,
   or `#` where pLDDT is below `structure.low_plddt_threshold` (70, strictly below, as
   upstream) and at residues outside the structure's range. The tokens are part of the cache
   key.

3. **SaProt is scored on the residues the structure models (`pdb_range`), in one unwindowed
   pass.** This departs from two existing rules, which now apply to ESM-2 only: the 1,022-residue
   windows (CLAUDE.md) and the MSA region (ADR 0012). It is what both upstream sources do, and
   the alternatives do not reproduce ProteinGym's scores. Per-variant Spearman against
   ProteinGym on a sample of the measured positions of each assay (11 to 40 positions):

   | Assay | Structure range, one pass | 1,024-token windows | MSA region |
   |---|---|---|---|
   | `SCN5A_HUMAN_Glazer_2019` (2,016 residues) | 1.000000 | 0.9827 | 0.1228 |
   | `POLG_CXB3N_Mattenberger_2021` (2,185) | 1.000000 | 0.9812 | 0.9798 |
   | `KCNH2_HUMAN_Kozek_2020` (1,159) | 1.000000 | 0.9966 | 0.2297 |
   | `BRCA1_HUMAN_Findlay_2018` (1,863) | 1.000000 | 0.9911 | same as the range |
   | `CAR11_HUMAN_Meitlis_2020_lof` (1,154) | 1.000000 | 0.9951 | same as the range |
   | `A0A140D2T1_ZIKV_Sourisseau_2019` (structure 291-794) | 1.000000 | fits one window | 0.9945 |

   With windows, two assays would fall below the gate's 0.99. With the MSA region, a 31- or
   32-residue input (KCNH2, SCN5A) has almost nothing in common with the scores of the full
   protein. The model has rotary position embeddings, so nothing in it limits the input to
   1,022 residues; 13 of the 213 regions are longer, up to 2,832 (BRCA2). `window_tokens: null`
   in the model config means "never window", and `max_batch_tokens` only limits how many masked
   copies share a forward call.

4. **A shared engine for masked language models.** `scoring.masked_lm.MaskedLMScorer` holds the
   cache handling, the two marginals and the windows; `Esm2Scorer` and `SaprotScorer` supply
   the tokenisation, the masking token and the amino-acid log-probabilities. ESM-2's numbers
   did not change: recomputed entries for 650M were bit-identical to the cached ones, and the
   three ESM-2 model gates give the same values as before.

5. **The checkpoint is pinned and loaded from the pinned file.** `westlake-repl/SaProt_650M_AF2`
   at revision `d9b9ad00ef61c0990e611b2b43f2231c7de24b38`. The repository holds only
   `pytorch_model.bin`; by default `transformers` then fetches an automatically converted
   `model.safetensors` from another ref of the repository (`refs/pr/5`), which is not the
   pinned revision. `use_safetensors=False` keeps the load on the pinned file.

6. **One definition of "the model's assays".** A model whose config has `plddt_threshold` is
   structure-aware. It is scored, gated and benchmarked on the reference minus
   `data.structure_exceptions`: 213 assays, 665,793 single mutants. Scoring fails unless the
   structure tables cover exactly that set. Paired comparisons with a sequence-only model run
   on the shared assays, and only `data.structure_exceptions` may be missing from one side.

7. **The model gate has documented exceptions** (`conf/eval/model_gate.yaml`, per model). For
   two assays ProteinGym's `SaProt_650M_AF2` scores were made from wrong inputs (below). They
   are compared and written to the gate's results with `exception = true`, and left out of both
   checks. The thresholds are unchanged (0.99 and 0.01). The list must describe the data, like
   `data.structure_exceptions`: the gate fails if a listed assay reaches 0.99 (a stale entry)
   and refuses a list that names an assay it did not compare. Every entry needs a replay test.

8. **The benchmark uses our scores for all 213 assays**, the two exceptions included.

9. **No wild-type marginal for SaProt in the benchmark.** Upstream defines none and ProteinGym
   has nothing to check one against. The scorer accepts `fast=True` (the `Scorer` protocol
   requires it), but the pipeline runs the masked marginal only for `masked_only_models`.

## The two upstream errors

The reference file ProteinGym used when its SaProt scores were made (the baseline was added on
2024-03-20; reference file at commit `4fed3d9`) differs from the pinned v1.3 file in the
structure columns of exactly three assays: these two, and `CAS9_STRP1_Spencer_2017_positive`,
which has no usable structure (ADR 0003).

| Assay | Then | v1.3 |
|---|---|---|
| `POLG_HCVJF_Qi_2014` | `pdb_range` 1981-2224 | 1982-2225 |
| `BRCA2_HUMAN_Erwood_2022_HEK293T` | three files, `1-1000\|1001-2085\|2086-2832` | one file, 1-2832 |

- **`POLG_HCVJF_Qi_2014` (1,630 single mutants).** The script pairs `target_seq[1980:2224]`
  with the 244 structure tokens of residues 1982-2225, so every residue carries the 3Di token
  of the next residue. Our scores from the correct range: Spearman 0.2770 against ProteinGym's.
  Our scorer with the shifted input: 1.000000, max |difference| 2.0e-05.
- **`BRCA2_HUMAN_Erwood_2022_HEK293T` (265 single mutants).** The script scores the rows of
  each chunk in turn, concatenates the three score arrays and assigns the result to the table
  in file order. The file is not sorted by position, so all 265 rows hold the score of another
  variant. Our scores: Spearman -0.0611 against ProteinGym's. Our scorer per chunk, with the
  same assignment: 1.000000, max |difference| 2.9e-05; per chunk with the scores on the right
  rows: -0.07.

ProteinGym's scores for these assays barely predict the experiment (Spearman with `DMS_score`
0.0005 for BRCA2 and 0.1732 for POLG_HCVJF); ours do (0.4626 and 0.4047). The replays are slow
tests in `tests/test_reproduction.py`. Upstream `main` (commit `144fe22`, 2026-03-25) has the
corrected reference rows and the same script, and v1.3 is still the latest score release, so
the published scores are the old ones. `docs/upstream/proteingym_saprot_issue.md` is a draft
report; it has not been posted.

## Results

Model gate, `saprot_650m_af2` against `proteingym:SaProt_650M_AF2`, single mutants:

| | Assays | Variants | Lowest per-variant Spearman | Aggregate ours | Aggregate theirs |
|---|---|---|---|---|---|
| Gated | 211 | 663,898 | 0.999695 | 0.4595 | 0.4595 |
| All compared | 213 | 665,793 | -0.0611 | 0.4600 | 0.4578 |

SaProt's aggregate Spearman, both ways:

| Scores | Rows | Assays | Spearman |
|---|---|---|---|
| Ours | single mutants | 213 | 0.4600 [0.4417, 0.4827] |
| ProteinGym's, same harness | the same rows | 213 | 0.4578 |
| ProteinGym's, same harness | single mutants | 217 | 0.4559 |
| ProteinGym's, same harness | all rows | 217 | 0.4566 |
| Published leaderboard | all rows | 217 | 0.457 |

On the same 213 assays our aggregate is 0.0022 above the one from ProteinGym's scores, and the
whole difference is the two assays above: without them both are 0.4595. The published 0.457
also includes multi-mutants and the four assays we do not score, so it is not comparable with
0.4600 and is never used as a target.

Other metrics on the 213 assays (212 for AUC and MCC, undefined in one assay): AUC 0.7567, MCC
0.3581, NDCG@10% 0.7774, top-10% recall 0.2126. Spearman by function type: Activity 0.4583,
Binding 0.4292, Expression 0.4816, OrganismalFitness 0.3660, Stability 0.5647.

SaProt 650M against ESM-2 650M, paired bootstrap on the 213 shared assays (10,000 resamples,
seed 0): Spearman 0.4600 against 0.4144, difference +0.0456 [+0.0283, +0.0625], positive in
every resample. By function type the difference is clear for Stability (+0.0685 [+0.0418,
+0.0952]), Expression (+0.0728 [+0.0333, +0.1063]) and Activity (+0.0319 [+0.0022, +0.0560]),
and not clear for Binding (+0.0502 [-0.0103, +0.1125], 13 assays) or OrganismalFitness (+0.0046
[-0.0156, +0.0208]). The intervals are not corrected for the number of comparisons.

Scoring run: 42,260 measured positions in 213 assays, 35,992 of them computed (21 assays share
a sequence and structure with an earlier one and were read from the cache), 6,293 s on MPS.

## One passing assay with a larger difference

`F7YBW8_MESOW_Ding_2023` passes (Spearman 0.999695) but its largest score difference is 0.099,
where every other gated assay is below 8e-05. The cause is one structure token: at residue 3
our Foldseek build (the pinned release 10-941cd33) gives 3Di state `P`, and ProteinGym's scores
correspond to `D`. Changing that one token in our input reproduces their scores to 1.3e-05; no
other single-token change comes closer than 0.068 (residues 1-74 were searched before the time
limit set for the diagnosis). ProteinGym does not record its Foldseek version, so this is read
as a difference between Foldseek builds at one residue of a flexible N-terminus. Nothing is
changed for it.

## Consequences

- SaProt results cover 213 assays and say so; comparisons with ESM-2 use those 213.
- A future ProteinGym release with corrected SaProt scores makes the gate fail until the stale
  entries are removed from `conf/eval/model_gate.yaml`.
- A gate exception costs a replay test and a written reason. It is not a way to pass a gate.
- The Nextflow step now passes the structure tables to every scoring task. The benchmark run
  of this ADR was made with the CLI commands directly; the pipeline's smoke test covers the new
  wiring with `esm2_8m` only, so a SaProt task has not yet run under Nextflow.
- Website landscapes for SaProt (full target, `#` outside the structure) are not built yet.
