# 0028 — The website bundle: one compressed file per protein

- Status: accepted
- Date: 2026-10-17
- Builds on: ADR 0012 (MSA region, website landscapes on the full target), ADR 0018 (full
  landscapes in the cache), ADR 0024 (CV+ intervals; CCDB), ADR 0025 (deployed landscapes and
  manifest), ADR 0026 addendum (pinned Hub revisions)

## Context

The website must work with the backend asleep (CLAUDE.md), so every protein page is a static
file the browser downloads once. Phase 3 left the inputs in three places: the supervised
landscapes, assay table, measurements and structure features in the dataset repo at
`publish.revision`; the final results tables in the model repo at `train.publish.revision`; and
the zero-shot landscapes only in the log-probability cache. Nothing yet said what the browser
receives, how big it may be, or how much rounding it may suffer.

## Decision

### 1. One gzip JSON file per assay, plus a manifest and the benchmarks

`mutascope export bundle` writes `build/bundle/`:

- `proteins/<assay_id>.json.gz`, one per ProteinGym assay (217). "Protein" means assay: the
  measurements, the supervised model and its intervals are per assay, and assays of one UniProt
  entry can have different targets. Each file holds the assay metadata (UniProt, function type,
  counts, structure range, `n_assays_for_protein`), the default model with its label and reason,
  the measured `DMS_score`, the zero-shot landscapes of ESM-2 650M (all) and SaProt (213 with a
  structure), per-residue pLDDT / relative SASA / contacts, the MSA region of the five
  MSA-region assays, the supervised block for the 198 eligible assays, and a version stamp.
- `manifest.json`: every assay, its default model and label ("Supervised (n = 9,576)" or
  "Zero-shot"), file size and sha256.
- `benchmarks.json`: zero-shot summaries of all eight score sets (including ESM-2 35M / 150M
  and the wild-type marginals, whose landscapes are not shipped), the shared-assay table, the
  zero-shot and supervised paired comparisons (also by assay size), the deployed model against
  SaProt per function type, the caveats, the `train uncertainty` coverage rows of the deployed
  design and the out-of-fold coverage of the deployed landscapes.
- `schema/`: JSON Schemas generated from the Pydantic models (`mutascope.export.format`), `$id`
  `urn:mutascope:bundle:1.0.0:<kind>`; the same files are committed in `docs/schema/bundle/` and
  a test fails when they go stale.
- `README.md` (from `docs/BUNDLE_CARD.md` plus the version stamp) and `SHA256SUMS`.

JSON rather than Arrow: the browser parses it natively, with no library, and gzip is undone by
`DecompressionStream`. Every per-variant quantity is an `(L, 20)` grid flattened row-major in
`AA_ORDER`, so the frontend has one indexing rule; `null` marks no value (the wild type of a
supervised grid, an unmeasured variant).

### 2. Zero-shot landscapes are the full-target ones; the MSA region is a label

ADR 0012 and 0018: the benchmark scores five assays on their MSA region (ESM-2) and three on
their structure range (SaProt); the website shows the full target. The bundle ships the
full-target cache entry for every assay and, for the five, `msa_region` (`label`, 0-indexed
`start`, exclusive `end`). Inside the region these scores differ from the benchmark's, because
the model saw a longer sequence; the label says which region the benchmark numbers describe.

### 3. Fixed-point numbers, with decimals chosen per assay

A number array is `{"decimals": d, "values": [int | null]}`, `value = q / 10**d`. Integers are
shorter than decimals in JSON and decode identically in Python and JavaScript (checked below
`2**53`). Zero-shot scores (nats) keep 3 decimals. The fields in `DMS_score` units of one assay
(measurement, prediction, all four bounds) share one `d`, the largest of:

- 3 (`dms_min_decimals`);
- a step of at most SD / 1000 (`dms_steps_per_sd`). A fixed 3 decimals would leave 16 steps per
  SD in `HSP82_YEAST_Cote-Hammarlof_2020_growth-H2O2` (SD 0.016; it gets 5);
- a step below the smallest non-zero gap between a measured value and its interval bounds,
  capped at 9 (`dms_max_decimals`). Rounding to the nearest step is monotone, so `y >= bound`
  survives it, but `y < bound` can become a tie, and bounds are inclusive. Values more than one
  step apart cannot tie, so with this step every coverage count is exactly the unrounded one.

float16 was rejected: its step grows with the value (0.0156 between 16 and 32), it is not a
JSON type, and the CCDB intervals below need 1e-9.

### 4. The rounding is proved harmless on the written files

Each file is written, read back, validated and decoded, and the decoded file is scored
(`mutascope.export.fidelity`, `results/export/bundle_fidelity.parquet`): per assay and
aggregated, the Spearman of every shipped landscape and of the supervised prediction against
the measurements, and the 80% / 90% coverage. Each must be within 0.001 (Spearman) and 0.002
(coverage) of the value from the unrounded inputs, and of the reported value where the bundle
holds the same numbers: zero-shot per-assay Spearman from `results/zero_shot_<model>_single`
on the 212 (ESM-2) and 210 (SaProt) assays whose benchmark region is the full target, and
coverage from the published `final_checks`. The supervised CV Spearman is pooled per fold on
each fold's training scale, so it has no counterpart in one landscape; it is compared with the
source only. Any failure, or a file over budget, leaves the previous bundle in place.

### 5. Inputs from the pinned Hub revisions, checked against their own checksums

`fetch_pinned` downloads the five dataset files and five model-repo tables at the pinned
commits and compares each with the `SHA256SUMS` published in the same commit; a branch name or
short hash is refused. Only the zero-shot landscapes (not on the Hub) and the zero-shot results
(`results/`) are local. The published final tables differ from the local `results/supervised`
copies by at most 1.2e-7 (the single-threaded re-baseline, `ed9cdf4`, landed after the
publication) and in their provenance stamps; the bundle reads the published ones.

### 6. Budgets set from measurement

Measured before setting them (decision 3's encoding, 2026-10-17): 217 files, 29,218,051 bytes
compressed (79,167,098 bytes of JSON), median 88,105, largest 1,153,890
(`A0A140D2T1_ZIKV_Sourisseau_2019`, 3,423 residues). Budgets (`export.budget`): 2,000,000 bytes
per protein file (the browser downloads one at a time) and 40,000,000 for all of them, about
35% above the measurement. The build fails and names every file over budget.

### 7. Published into the dataset repo, tagged and pinned

`mutascope export push` uploads the bundle to `bundle/` of the dataset repo
(`publish.repo_id`) as one commit whose deletions are confined to that folder, then tags it
(`export.publish.tag`, `bundle-v1.0.0`). An existing tag is refused: a tag names one bundle. The
commit is pinned as `export.publish.revision` for the API and the website, like the Task 0 pins.

### 8. Caveats come from the paired comparisons

A function type gets a caveat when, under some split, the deployed model's paired 95% CI against
SaProt does not lie above 0. Today that is Binding (13 assays) under contiguous (+0.010, CI
−0.106 to +0.121) and modulo (+0.076, CI −0.033 to +0.172), the weak point of ADR 0026, now
stated by the rule rather than by hand.

## Evidence

**The first encoding failed the proof.** With only the SD rule (26,088,727 bytes, largest
952,611), 5 numbers in 3 assays moved beyond tolerance:

| Assay | Number | Change | Cause |
|---|---|---|---|
| `CCDB_ECOLI_Adkar_2012` | coverage 80% / 90% | +0.200 / +0.099 | one score per position (ADR 0024): random-split out-of-fold predictions equal the measurement to ~1e-6 and intervals are 3.0e-6 wide; at 3 decimals all 1,176 variants tie a bound |
| `CCDB_ECOLI_Adkar_2012` | supervised Spearman | 0.0021 | the same: predictions at a position differ by ~1e-6 and collapse to one value |
| `EPHB2_HUMAN_Tsuboyama_2023_1F0M` | coverage 80% | 0.0024 | 3 of 1,239 measured variants just outside a bound tied it |
| `LYAM1_HUMAN_Elazar_2016` | coverage 90% | 0.0028 | 1 of 359 |

A per-assay tolerance of 0.002 allows no tie in an assay under 500 variants, so the gap rule
(decision 3) replaced "make ties rare" with "make ties impossible". It costs 12% (29,218,051
bytes against 26,088,727). Decimals chosen for the `DMS_score` fields: 3 in 36 assays, 4 in 120,
5 in 52, 6 in 6, 7 in 2, 9 in 1 (CCDB, at the cap; its numbers pass).

**The bundle as published** (`results/export/bundle_fidelity.parquet`):

| Quantity | Assays | Max change per assay vs source | vs reported | Aggregate, source → bundle |
|---|---|---|---|---|
| ESM-2 650M Spearman | 217 | 4.30e-4 | 4.30e-4 (212) | 0.413548 → 0.413539 |
| SaProt 650M Spearman | 213 | 6.56e-4 | 6.56e-4 (210) | 0.460112 → 0.460109 |
| ridge `seq_struct` Spearman | 198 | 4.04e-4 | — | 0.736517 → 0.736515 |
| coverage 80% | 198 | 0 | 0 | 0.800296 (= reported) |
| coverage 90% | 198 | 0 | 0 | 0.900342 (= reported) |

The largest Spearman change is ENVZ_ECOLI_Ghose_2023 under SaProt (0.147826 → 0.147170), from
ties at 3 decimals in a landscape with Spearman near 0.15. The aggregates here are over the
full-target landscapes; the benchmark's (ESM-2 650M 0.4125 on single mutants) uses the MSA
region for five assays and is not what this table recomputes.

## Consequences

- New: `mutascope export bundle | push | schema`, config group `export`, modules
  `export.{format,bundle,assemble,fidelity,benchmarks,schemas}`, `fetch_pinned` and
  `push_bundle` in `export.hub`. New direct dependency: `pydantic` (already installed through
  pandera; the bundle format is defined with it).
- The API and the frontend read the bundle by `export.publish.revision`, never by branch, and
  generate their types from `docs/schema/bundle/`.
- `calibration_scheme` is per variant, not per residue: a residue's only measured variant is
  calibrated on contiguous folds while its unmeasured substitutions use random ones
  (`train.final.scheme_by_position` counts *other* measured variants). The bundle stores it as
  a coded grid.
- The export is not a Nextflow process yet: it reads published revisions, not the pipeline's
  outputs, so wiring it belongs with the Docker and CI work that consumes it.
- No MLflow run: the command evaluates no model; its numbers are copies or rounding checks,
  and they are in `results/export/`.
- A rebuild at another commit changes `code_version` in every file, and so every checksum. A new
  bundle on the Hub needs a new tag.
