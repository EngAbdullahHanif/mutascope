<!--
Draft of an issue for https://github.com/OATML-Markslab/ProteinGym. NOT POSTED.
Checked against upstream main at commit 144fe22 (2026-03-25): the reference file already has
the corrected rows, the SaProt script is unchanged, v1.3 is still the latest score release, and
no existing issue covers this. So this is a new report, not a comment.
-->

**Title:** SaProt (650M, AF2) zero-shot scores for `POLG_HCVJF_Qi_2014` and `BRCA2_HUMAN_Erwood_2022_HEK293T` appear to come from an older reference file

Hello, and thank you for maintaining ProteinGym.

While reproducing the `SaProt_650M_AF2` substitution scores from the v1.3 release
(`zero_shot_substitutions_scores.zip`), I matched 211 of the 213 assays that have a structure
to float precision (per-variant Spearman ≥ 0.9997, typically max |difference| around 2e-5).
Two assays do not match, and in both cases the published scores can be reproduced exactly by
using the structure columns of an older `reference_files/DMS_substitutions.csv` (for example
commit `4fed3d9`, March 2024, around when the SaProt baseline was added). The reference file
has since been corrected, so I think only the score files are out of date.

Setup: `westlake-repl/SaProt_650M_AF2`, `proteingym/baselines/saprot/compute_fitness.py` logic
(tag `PG_v1.3`, commit `1f8de97`), Foldseek release 10-941cd33, single mutants only.

### 1. `POLG_HCVJF_Qi_2014`: `pdb_range` was off by one

| | `pdb_range` |
|---|---|
| Older reference file | `1981-2224` |
| v1.3 / current `main` | `1982-2225` |

`calc_fitness` zips `target_seq[pdb_range]` with the structure tokens of `POLG_HCVJF.pdb`
(244 residues, which are residues 1982-2225). With the old range, each residue of 1981-2224 is
paired with the 3Di token of the following residue.

| Input | Spearman vs published `SaProt_650M_AF2` (1,630 single mutants) | max \|diff\| |
|---|---|---|
| Range 1982-2225 (current reference) | 0.277 | 12.8 |
| Sequence 1981-2224 with the tokens of 1982-2225 | 1.000000 | 2.0e-05 |

Spearman with `DMS_score`: 0.173 for the published scores, 0.405 with the current range.

### 2. `BRCA2_HUMAN_Erwood_2022_HEK293T`: scores assigned to the wrong rows

The older reference file listed three structure chunks
(`BRCA2_HUMAN_1-1000.pdb|BRCA2_HUMAN_1001-2085.pdb|BRCA2_HUMAN_2086-2832.pdb`, ranges
`1-1000|1001-2085|2086-2832`); the current one has a single file, `1-2832`.

In `main()`, the rows are filtered per chunk, the per-chunk score arrays are concatenated, and
the result is assigned back with `DMS_data['SaProt_score'] = model_scores`. That assignment is
positional, and the DMS file is not sorted by position, so with more than one chunk each row
receives the score of a different variant. For this assay all 265 rows are affected.

| Input | Spearman vs published `SaProt_650M_AF2` (265 single mutants) | max \|diff\| |
|---|---|---|
| Single structure 1-2832 (current reference) | -0.061 | 13.2 |
| Three chunks, scores placed on the correct rows | -0.07 | 13.1 |
| Three chunks, concatenated scores assigned in file order | 1.000000 | 2.9e-05 |

Spearman with `DMS_score`: 0.0005 for the published scores, 0.463 with the current reference.

### Effect and suggestion

On the 213 assays with a structure (single mutants, ProteinGym aggregation), the average
Spearman computed from the published scores is 0.4578; with these two assays rescored it is
0.4600. The effect on the leaderboard is small, but the per-assay values for these two DMS
change a lot.

- Rescoring the two assays with the current reference file would fix the published scores.
  `SaProt_35M_AF2` was presumably produced by the same script and reference file and may be
  affected in the same way; I have not checked it.
- The positional assignment in `main()` is still present on `main`. It is harmless while no
  assay has more than one structure file, but assigning by row index (for example writing the
  scores into `filtered_DMS_data` and concatenating the frames) would make it safe.

I am happy to share the replay script or the rescored values for the two assays if useful.
