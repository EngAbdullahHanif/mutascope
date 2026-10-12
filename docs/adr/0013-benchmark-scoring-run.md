# 0013 — Benchmark scoring run: measured positions only, resumable from the cache

- Status: accepted (scope decided by the author, 2026-10-12)
- Date: 2026-10-12
- Supersedes: ADR 0012, decision 9 ("score every position"), for benchmark runs

## Context

ADR 0012 planned to compute masked marginals at every residue of every target in one step
(about 4 hours for the 650M model), because the website needs full landscapes. The benchmark and
the model gate need far less: only the positions the assays measure. On the pinned release that
is 37,753 of the 62,671 residues in the 187 distinct sequences and scoring regions the 217
assays use. The author decided to score only those positions now and to keep the stored array
in its full `(L, 20)` shape, so that nothing downstream changes shape when the rest is filled in.

The run is long on a laptop, so it must survive a crash or the machine sleeping: a rerun must
not score again what is already on disk.

## Decision

1. **`positions` on the scorer.** `Scorer.landscape(..., positions=[...])` scores only those
   residues (0-indexed in the scored sequence); `None` scores all. The result is always
   `(L, 20)`. A row that was never scored is NaN in every column, the wild-type column
   included, so "not scored" cannot be read as a score of 0.
2. **One cache entry per sequence, filled in incrementally.** The cache key is unchanged
   (ADR 0012). An entry is the `(L, 20)` log-probabilities with NaN rows for residues not scored
   yet. A request runs the model only for the requested rows the entry lacks, then rewrites the
   entry. The alternative, putting the position set in the key, was rejected: two assays of the
   same protein with different positions would each pay for their overlap, and the website's
   full landscape could not reuse the benchmark rows.
3. **The cache is the checkpoint.** An entry is written, by a rename, as soon as its sequence
   is done. The cache directory (`scoring.cache_dir`, under `data/`) is outside the output
   directory and outside Nextflow's work directory. A rerun, with or without `-resume`, reads
   finished sequences from disk. The unit of loss is the one sequence in progress: at most
   455 s on this run (Cas9, 1,368 positions). The score partitions themselves are rebuilt from
   the cache on every run, which takes seconds, so there is no second resume mechanism to keep
   consistent with the first.
4. **Fail before scoring, and never write a NaN score.** `score_assay` checks every mutant
   (wild type, inside the region) before the model runs, and fails if a requested position
   comes back NaN. `ZeroShotScoreSchema` requires a finite score for every variant.
5. **A computation log for honest run times.** Each computation appends one line to
   `computations.jsonl` in the cache (entry, positions, seconds). A resumed run's report shows
   only what that invocation did; the log keeps the total across interruptions.
6. **One Nextflow task.** `SCORE_ZERO_SHOT` runs `mutascope score zero-shot` once for all
   assays (`maxForks 1`): one model load and one GPU. Per-assay tasks would add 217 model loads
   and no resumability the cache does not already give.
7. **The model gate is one function, used by the CLI and the test.** `eval/gate.py` implements
   ADR 0010's two checks; `mutascope eval model-gate` reports them and
   `tests/test_reproduction.py` asserts them. The two score tables must contain exactly the
   same variants, and an undefined per-assay agreement counts as a failure. The thresholds
   (0.99, 0.01) are unchanged and now live in `eval.model_gate`.

## Evidence

**Resuming.** Unit tests cover a model that dies in its third sequence and a runner that dies
in its second assay. On the real run (650M, MPS), the process was killed with `SIGKILL` 298 s
in, partway through assay 5. Four entries were on disk and no temporary file was left. The
rerun reported 0 positions computed and 0.0 s for assays 1–4, scored assay 5 in full
(564 positions, 93.5 s) and read assay 6, another assay of the same protein, from the cache.
It was killed again after 105 s; the full pipeline run that followed read all six from the
cache.

**The run** (M4 Pro, MPS, `caffeinate -ims make pipeline`):

| | |
|---|---:|
| Assays / single mutants scored | 217 / 696,311 |
| Distinct sequences and regions (cache entries) | 187 |
| Positions scored / residues in those sequences | 37,753 / 62,671 |
| Full pipeline, wall clock | 1 h 36 min (5,776 s) |
| Scoring step in that run | 5,725 s; 35,775 positions; 28 assays from the cache |
| Model time over all runs (computation log) | 6,081 s (1 h 41 min) |
| Assays with a region longer than 1,022 residues | 11, taking 774 s |
| Slowest assay | `CAS9_STRP1_Spencer_2017_positive`, 1,368 positions, 455 s |

The 6,081 s include the 359 s (1,978 positions) the two interrupted runs had finished. The
25 s spent on assay 5 before the first kill were lost and are not counted.

**Model gate**, against ProteinGym's precomputed `ESM2_650M` on the same single mutants:

| Check | Result | Limit |
|---|---:|---:|
| Per-variant Spearman, lowest of 217 assays | 0.99999996 | ≥ 0.99 |
| Assays below the limit | 0 | 0 |
| Largest absolute score difference, any variant | 2.35e-04 (`RDRP_I33A0_Li_2023`) | — |
| Aggregate Spearman vs `DMS_score`, ours / theirs | 0.4125 / 0.4125 | \|Δ\| ≤ 0.01 |

The largest per-assay difference between our Spearman against `DMS_score` and theirs is
9.9e-06. The five MSA-region assays (ADR 0012) agree like the rest (largest difference
1.2e-04), as do the 11 long ones (1.4e-04). The scores are not identical to ProteinGym's,
which is what an independent run of the same checkpoint on different hardware should give:
22 assays have a variant that differs by more than 1e-04, none by enough to change a rank
correlation in the seventh decimal.

## Consequences

- `results/zero_shot_esm2_650m_single*.parquet` hold our first own benchmark numbers (single
  mutants; Spearman 0.4125, 95% CI 0.389–0.441). They are never compared with the published
  0.414, which includes multi-mutants (ADR 0010).
- The cache now holds partial entries. Anything that needs a full landscape must call the
  scorer with `positions=None`, which fills in the remaining 24,918 residues (plus the full
  targets of the five MSA-region assays); it must not read `.npy` files directly.
- With a warm cache the slow model-gate test takes about 10 s; on an empty cache it runs the
  whole scoring, about 1 h 40 min.
- A process killed during the few milliseconds of a cache write can leave a `*.npy.tmp` file.
  It is never read; nothing removes it yet.
- The pre-commit hook stashes unstaged changes while it runs, which briefly swaps source files
  under a running pipeline (the package is an editable install). Do not commit during a run.
