# 0020 — Feature store: every substitution, per-residue arrays stored once, no labels

- Status: accepted
- Date: 2026-10-14
- Builds on: ADR 0018 (full landscapes in the cache), ADR 0019 (feature builders see keys only),
  ADR 0012 / 0015 (benchmark regions), ADR 0006 (structure features)

## Context

The supervised models (augmented ridge, LightGBM, MLP) need per-variant features: one-hot
wild type and mutant, BLOSUM62, relative position, zero-shot scores and the 20
log-probabilities at the position from ESM-2 650M and SaProt 650M, per-position embeddings
from both, and the structure features. The final models predict the full landscape, so the
features must exist for all 19 × L substitutions of every target, not only the 696,311
measured ones.

Three constraints shaped the design:

- **Compute.** Embeddings need a model pass. One pass per variant would be about 1.6M passes;
  one unmasked pass per protein gives every residue's embedding at once.
- **Size.** The matrices, assembled in float32 for every substitution, would be large.
- **Leakage.** Under ADR 0019, a feature builder never sees `DMS_score` or `DMS_score_bin`.

## Estimate (made before building)

Counted from the same landscape targets `score landscapes` uses:

| | ESM-2 650M | SaProt 650M (AF2) |
|---|---:|---:|
| Assays | 217 | 213 |
| Distinct cache entries = unmasked passes | 192 | 188 |
| Forward calls (≤ 1,024-token tiles for ESM-2) | 220 | 188 |
| Tokens through the model | 92,231 | 75,128 |

The 192 ESM-2 entries are the benchmark regions of the 187 distinct targets plus the full
targets of the 5 MSA-region assays. The 188 SaProt entries are the benchmark regions (the residues
the structure models) plus the full targets of the 3 partial-structure assays, and assays of
one protein with different structure tokens are separate entries. Scores and log-probabilities
need **no** pass: they are in the masked-marginal cache, complete at every residue
(`check-landscapes`: 882/882).

| Feature set | Assays | Variants (19 × L) | Columns | Assembled, float32 |
|---|---:|---:|---:|---:|
| `seq` | 217 | 1,637,401 | 1,343 | 8.8 GB |
| `seq_struct` | 213 | 1,589,008 | 2,647 | 16.8 GB |

Both are below the 20 GB limit set for the task, but 98% of each row is per-residue data
(log-probabilities, embedding, structure) repeated for the 19 substitutions of a residue.

## Decision

### 1. Store per-residue data once per residue; assemble rows per assay on demand

`processed/features/` has three parts, partitioned by `assay_id`:

- `variants/`: the 19 × L substitutions, ordered by position then mutant in `AA_ORDER`, with
  the per-variant scalars `rel_position`, `blosum62` and `score_<model>`
  (`FeatureVariantSchema`);
- `positions/`: one row per residue with `plddt`, `rel_sasa`, `contact_count` and
  `region_<model>` (`FeaturePositionSchema`);
- `arrays/`: `<model>.log_probs.npy` `(L, 20)` and `<model>.embedding.npy` `(L, 1280)`,
  float32, row i = residue i of the target.

`features.sets.assemble(store, feature_set, assay_id, keys)` builds one assay's float32
matrix. It repeats each residue's arrays for that residue's variants, so a model never holds
more than one assay. One-hot columns are computed when a matrix is assembled, from `wt_aa` and
`mt_aa`, which are stored.

A variant is identified by `(position, mt_aa)`, 0-indexed. The store never formats or parses a
mutation string, so `parse_mutation` stays the only place positions are converted (data rule 2).

### 2. Embedding: mean of the last 4 `hidden_states`, one unmasked pass per cache entry

The embedding of residue i is the mean of the last 4 entries of Hugging Face's `hidden_states`
at token i + 1. Entry 0 is the embedding output and entry k is block k's output. The last entry
is taken after the final LayerNorm, as in fair-esm's `representations[33]`: for the 33-block
650M models that means blocks 30–32 plus normalised block 33. A test checks this against a
direct `output_hidden_states` call on a small random ESM. Sequences over 1,022 residues
(ESM-2) use the same half-overlapping tiles as the wild-type marginal (ADR 0012), and each
residue is read from its most central tile. SaProt is never windowed (ADR 0015).

Embeddings live in their own cache (`features.embedding_cache_dir`), in the log-probability
cache's layout: one `.npy` per `sha256(model, revision, sequence, struct tokens, method,
region)`, with method `embedding_mean_last4`. Each entry is written after its pass, so a rerun
computes nothing. The scorer's code is not changed: `features.embeddings` only borrows its
tokenizer, model and window, so the gate-verified scoring path is untouched.

### 3. Inside the benchmark region, the benchmark landscape; outside it, the full target

Each model has a benchmark-region landscape per assay and, where that region is partial, a
full-target one (ADR 0018). A residue inside the benchmark region reads every model array
(log-probabilities, score, embedding) from the benchmark entry. A residue outside it reads the
full-target entry. `positions.region_<model>` records this per residue. **Every measured
variant lies inside its model's benchmark region** (0 of 696,311 outside for ESM-2, 0 of
665,793 for SaProt), so training rows always see the scores the model gate verified. The
full-target entries feed only unmeasured residues, in 5 ESM-2 and 3 SaProt assays.

This has a cost: for those assays, a landscape predicted outside the benchmark region uses
features computed from a different context than the training rows. The number of affected
residues is printed by `features build` and recorded in `feature_store.parquet`
(`residues_from_full_target`).

### 4. Labels are never read

`features build` reads `assay_id, position, wt_aa, mt_aa` from the processed variants. They
are used only to check that every measured variant is a store row with the same wild type;
a mismatch fails the build and lists the variants (data rule 3). A test builds the store from
two variant tables that differ only in their labels and requires byte-identical output.

### 5. Feature sets are declared in config

`conf/features/default.yaml`:

- `seq` (217 assays): `onehot, blosum62, rel_position, esm2_650m:{score, log_probs,
  embedding}`, 1,343 columns;
- `seq_struct` (213 assays): `seq` plus `saprot_650m_af2:{score, log_probs, embedding}` and
  `structure`, 2,647 columns.

A set with `structure: true` refuses an assay without a structure rather than filling it with
NaN. In `seq_struct`, structure features are NaN only at unmeasured residues outside the
structure (the 3 partial-structure assays). Every other block must be finite, or the assembly
fails.

## Evidence

**Build** (M4 Pro, MPS, float32):

| Step | Passes | Model time | Wall (incl. model load) |
|---|---:|---:|---:|
| `features embed model=esm2_650m` | 192 (74,487 residues) | 31 s | 39 s |
| `features embed model=saprot_650m_af2` | 188 (74,752 residues) | 27 s | 36 s |
| `features build` (no model) | — | — | 8.5 s (6 s writing) |

A rerun of `embed` computes nothing; `build` replaces the store whole.

**Size on disk:** store 900 MB (`variants` 15.5 MB, `positions` 1.4 MB, `arrays` 883 MB),
plus 730 MB of embedding cache. The arrays are per assay, so assays of one protein each hold a
copy (86,179 rows against 74,487 distinct residues). The largest assay
(`A0A140D2T1_ZIKV_Sourisseau_2019`, 3,423 residues, 65,037 variants) assembles in 0.2 s:
0.69 GB for `seq_struct`.

**Checks on the real store:**

- *Scores are the benchmark's.* For every measured variant, `score_esm2_650m` and
  `score_saprot_650m_af2` equal the committed zero-shot score sets exactly: 696,311 of 696,311
  and 665,793 of 665,793, max |Δ| = 0.
- *Embedding rows are aligned with residues.* A logistic regression from embedding row i to
  the wild type at residue i, trained on 30 proteins and tested on 30 others (15,103
  residues), reaches 99.8% accuracy (ESM-2) and 100.0% (SaProt). The same probe on row i + 1,
  an off-by-one, reaches 77.8% and 82.7%: neighbouring residues share context, but the aligned
  row is clearly the right one.
- *Full-target residues.* 10,868 residues in 6 assays read a full-target landscape for at
  least one model (ZIKV 2,919, POLG_HCVJF 2,927, SCN5A 1,984, POLG_CXB3N 1,324, KCNH2 1,128,
  BRCA2 586). None has a measured variant.
- *Structure gaps.* 6,294 residues of the 213 structure assays have no structure features (the
  3 partial-structure assays). Again, none has a measured variant.

## Alternatives rejected

- **Materialise every row** (8.8 + 16.8 GB). It would fit, but each epoch or tree would read
  the same 1,280 numbers 19 times, and the training code would need memory-mapping to fit in
  24 GB.
- **Embeddings from masked passes** (the cached masked marginal's context). This would mean
  re-running the 1,562-slice landscape job with hidden states kept: about 11 hours instead of
  minutes. It would also not be the "one unmasked pass" representation the task asks for.
- **Full-target landscapes everywhere.** One rule for all residues, but for the 5 MSA-region
  assays it changes training features away from the benchmark scores (`KCNH2_HUMAN_Kozek_2020`:
  Spearman 0.36 against ProteinGym on the full target, ADR 0012).
- **Storing one-hot columns.** They are a pure function of `wt_aa` and `mt_aa`; storing them
  adds 260 MB and a second copy that could disagree.
- **The ensemble score as a feature.** Its z-score is computed over an assay's measured
  variants (ADR 0016), so it has no fixed value for unmeasured ones. The member scores carry
  the same information.

## Consequences

- `train cv` still uses the `none` feature set. Wiring `seq` / `seq_struct` into the CV loop
  needs `cv_metrics_table` to assemble features one assay at a time instead of taking one
  matrix for all assays (14.7 GB in float64 for `seq_struct`). That comes with the first real
  model (augmented ridge).
- Scalers and imputation of the structure NaNs belong inside the training folds, like any fitted
  transform (ADR 0019).
- `features embed` and `features build` are CLI commands but not Nextflow processes yet, like
  `data folds` (ADR 0019).
