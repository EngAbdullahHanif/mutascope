# 0018 — Full landscapes: every residue, resumable inside a sequence, with a coverage check

- Status: accepted
- Date: 2026-10-13
- Closes: the Phase 2 item left open by ADR 0017 ("full masked landscapes at every position")
- Changes: ADR 0013, decision 3 (the unit of loss is no longer one sequence, for this run);
  ADR 0007's smoke test (its tasks now share one cache)

## Context

The benchmark scores only the positions the assays measure (ADR 0013). The website shows every
substitution of a protein, so the Phase 4 export needs a masked marginal at every residue. Two
things were missing from the cache:

- the unmeasured residues of the benchmark entries;
- a full-target landscape for the assays whose benchmark region is only part of the target,
  which had no cache entry at all.

The second group is five assays for ESM-2 (the MSA-region assays of ADR 0012) and three for
SaProt, whose benchmark region is the residues the structure models (ADR 0015):
`A0A140D2T1_ZIKV_Sourisseau_2019` (291–794 of 3,423), `BRCA2_HUMAN_Erwood_2022_HEK293T`
(1–2,832 of 3,418) and `POLG_HCVJF_Qi_2014` (1,982–2,225 of 3,033).

Three constraints came with the task. Benchmark results must not change. The run is about half
a day on the laptop, so an interruption must not lose work. And "complete" must be something a
command checks, not something a run log says.

## Decision

1. **A landscape is a cache entry; nothing new is stored.** `mutascope score landscapes` asks
   the scorer for the residues an entry lacks, and the scorer writes them into the same
   `(L, 20)` entry (ADR 0013, decision 2). There is no second copy of the scores that could
   disagree with the benchmark's.

2. **Each assay has its benchmark-region landscape, and a full-target one where that differs.**
   Both are computed in full. They are separate entries, told apart by the region label in the
   cache key (`msa:281-804` or `pdb:291-794` against `full`), and the tables carry two columns,
   `benchmark` and `full_target`; both are true for the 212 (ESM-2) and 210 (SaProt) assays
   where the benchmark region is the full target. The two landscapes of one assay hold
   different numbers for the same residue, because the model sees a different input, so neither
   can stand in for the other: the benchmark one is what the gate verified, the full-target one
   is what the website shows.

3. **SaProt's full-target landscape uses `#` outside the structure, in one unwindowed pass.**
   This is the input `scoring.struct_tokens` already builds (ADR 0015, decision 2), run the way
   SaProt is always run. For the three assays above it means sequences of 3,033 to 3,423
   residues of which the structure models 8.0% to 82.9%; after the pLDDT rule only 6.0% to
   16.5% keep a 3Di letter (`POLG_HCVJF` 181 of 3,033, ZIKV 472 of 3,423, BRCA2 565 of 3,418),
   so these landscapes are close to sequence-only. These three landscapes have
   no upstream scores to be checked against; the benchmark-region landscapes of the same
   assays do.

4. **The work is cut into slices, and every slice is one cache write.** The runner asks the
   scorer for `scoring.landscapes.checkpoint_tokens` (131,072) tokens of model work at a time:
   128 residues of a windowed ESM-2 sequence, 38 residues of a 3,423-residue SaProt sequence.
   Each request is an ordinary `Scorer.landscape(..., positions=slice)` call, which writes the
   entry atomically before it returns. A run killed at any moment loses the slice in progress
   and nothing else. Under ADR 0013 the unit of loss was a whole sequence, which was fine at
   455 s and is not at the 82 minutes a 3,423-residue SaProt sequence took. The scorer itself
   did not change: slicing lives in the runner, so the benchmark's code path is untouched.

5. **`positions=[]` means "compute nothing, return what is known".** The runner uses it to ask
   an entry what is missing, through the scorer and not by reading `.npy` files (ADR 0013). It
   already behaved this way; it is now documented in the protocol and tested.

6. **Progress and remaining time are logged after every slice.** `landscape_progress` gives
   positions done of the total, the share of the work, elapsed time and the estimated time
   remaining. Work is counted in tokens run through the model (a pass over a 100-residue
   sequence is not the same work as one over a 1,024-token window), and the estimate is this
   invocation's elapsed time scaled by the work left. It is an estimate: see "Evidence" for how
   far off it was.

7. **A model-free check.** `mutascope score check-landscapes` builds, for every model in
   `scoring.landscapes.models`, the list of landscapes it owes and reads each from the cache by
   its key. A residue counts as scored only if all 20 values are finite. It writes
   `landscape_coverage.parquet` (one row per model, assay and landscape) and exits 1 if any
   landscape is incomplete. It loads no model, so it runs in seconds and can run anywhere the
   cache is. A structure-aware model is checked on the assays with a structure (213; ADR 0003).

8. **In the pipeline, on by default.** `LANDSCAPES` runs once per model after every
   `SCORE_ZERO_SHOT` task has finished (the two steps write the same entries, and there is one
   GPU), then `CHECK_LANDSCAPES` runs once. `--landscapes false` skips both. With a warm cache
   the step costs four model loads. The smoke test's tasks now share one cache
   (`params.cache_dir`, deleted by `make pipeline-test` before each run): with a cache per task,
   as before, the check could not see what the landscape task wrote.

## Alternatives rejected

- **Checkpointing inside the scorer** (write the entry every N batches in
  `MaskedLMScorer.log_probs`). Same protection, but it changes the code path the model gate
  verified and every scorer would have to implement it. Slicing in the runner needs nothing
  from a scorer beyond the protocol.
- **Only the full-target landscape for the partial-region assays**, leaving their benchmark
  entries with unmeasured rows. Cheaper by 52 minutes of SaProt time (BRCA2's 2,779
  unmeasured residues in `pdb:1-2832`, 3,125 s). Rejected because "the landscape the benchmark verified" would then be
  incomplete for exactly the assays where it differs from the website's.
- **Windowing SaProt's 3,400-residue full targets.** Faster, but SaProt is never windowed
  (ADR 0015): windows moved two assays below 0.99 against the official scores.
- **A written landscape table under `data/processed/`.** The Phase 4 export will write the
  served files; an intermediate copy would be a second source of truth.

## Evidence

**What was missing**, from `check-landscapes` on the cache before the run (all counts are
distinct cache entries; assays of one protein share an entry):

| Model | Entries | Residues | Missing | of which full targets with no entry |
|---|---:|---:|---:|---:|
| ESM-2 35M / 150M / 650M (each) | 192 | 74,487 | 36,734 | 11,816 (5 MSA-region assays) |
| SaProt 650M (AF2) | 188 | 74,752 | 38,760 | 9,874 (3 partial-structure assays) |

36,734 = the 24,918 unmeasured benchmark residues of ADR 0017 + 11,816; 38,760 = 28,886 +
9,874.

**The run** (M4 Pro, MPS, `caffeinate -ims`, models one after another; model time from the
cache log, one line per slice):

| Model | Positions | Slices | Model time |
|---|---:|---:|---:|
| ESM-2 35M | 36,734 | 323 | 2,123 s (35 min) |
| ESM-2 150M | 36,734 | 323 | 2,931 s (49 min) |
| ESM-2 650M | 36,734 | 323 | 9,926 s (2 h 45 min) |
| SaProt 650M | 38,760 | 593 | 25,847 s (7 h 11 min) |
| Total | 148,962 | 1,562 | 40,827 s (11 h 20 min) |

The six SaProt entries over 2,000 residues took 19,317 s, 75% of its time; the full
3,423-residue ZIKV target alone took 4,938 s (1.44 s per position).

**Interruptions.** The run was stopped four times: once on purpose to test resuming
(`SIGKILL` 75 s into ESM-2 35M), and three times on request (the first for a flat battery)
(150M at 20,289 of 36,734; 650M at 12,201, then at 19,072 of the 24,533 left). Each restart
planned exactly the remainder (35,818; 16,445; 24,533; 5,461 positions), and the positions in
the cache log add up to exactly the missing count for every model: no residue was scored twice,
and each stop lost only the time of the slice in progress (under a minute). No `*.tmp` file was
left.

**The time estimate.** First estimates against the actual time of the same invocation:
ESM-2 35M 36 min (actual 34 min), ESM-2 650M 2 h 51 min (actual 2 h 45 min over its three
invocations), SaProt 7 h 25 min after 52 s (actual 7 h 10 min). At 15:20, with SaProt 60% done,
the log said 2 h 58 min remaining; it finished 2 h 45 min later. A fit of time per position on
SaProt's slices (quadratic in length: 0.34 s at 1,000 residues, 1.44 s at 3,423) said 2 h 48
min. The estimate was high, not low, because the longest sequences happened to come early.

**Coverage after the run** (`results/data/landscape_coverage.parquet`):

| Model | Landscapes complete | Assays | Full-target beside a partial benchmark region | Residues |
|---|---:|---:|---:|---:|
| ESM-2 35M | 222 / 222 | 217 | 5 | 87,733 / 87,733 |
| ESM-2 150M | 222 / 222 | 217 | 5 | 87,733 / 87,733 |
| ESM-2 650M | 222 / 222 | 217 | 5 | 87,733 / 87,733 |
| SaProt 650M | 216 / 216 | 213 | 3 | 87,212 / 87,212 |

(Residues here count per assay, so a protein with several assays counts several times.)

**Benchmark unchanged**, checked three ways:

1. *Cache rows.* The cache was copied before the run. All 337,403 rows scored in it then are
   bit-identical after the run, in all 1,311 entries.
2. *Score sets.* `mutascope score zero-shot` rerun for the four masked score sets read every
   assay from the cache (0 positions computed) and wrote scores exactly equal to the committed
   benchmark's (696,311 variants for each ESM-2 size, 665,793 for SaProt; `check_exact`).
3. *Gates.* `make test-slow` (four model gates and the two SaProt replays) passes, 12 tests in
   244 s, with no tracked file under `results/` changed.

## Consequences

- The Phase 2 done-when line is now met in full; the roadmap item is ticked.
- The Phase 4 export can read every landscape from the cache without running a model. It must
  build its keys the way `landscape_coverage` does and should run `check-landscapes` first.
- The cache grows from 37 MB to 41 MB (1,311 to 1,329 entries). It is still not in git and not on the Hub; losing it
  costs the model time above.
- `make pipeline` on an empty cache now takes about half a day longer. `--landscapes false`
  gives the old behaviour.
- `results/data/landscape_run_<model>.parquet` describes one invocation, like the
  `zero_shot_run_*` files: a resumed run's file shows only what that invocation did. The cache
  log (`computations.jsonl`) has one line per slice and keeps the total.
- The time estimate assumes time proportional to tokens. Per token, SaProt's longest
  sequences are slower than its short ones, so the estimate errs by however much of that work
  is still ahead; on this run it stayed within 10% of the actual time.
