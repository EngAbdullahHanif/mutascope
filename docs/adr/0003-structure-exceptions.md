# 0003 — Explicit exceptions for assay → structure mapping

- Status: accepted
- Date: 2026-10-09

## Context

Structure-aware scoring (SaProt 3Di tokens, structural features) needs each assay's AF2 structure
to model exactly its `target_seq`. `mutascope data check-structures` accepts a mapping only if the
PDB's sequence is identical to `target_seq[pdb_range]`. On ProteinGym v1.3, 213 of 217 assays pass.
The other 4 fail because of inconsistencies in the upstream data, not because of our code:

| Assay | Problem |
|---|---|
| `CAS9_STRP1_Spencer_2017_positive` | The PDB has a 22-residue C-terminal tag (NLS + HA), and `pdb_range` 1-1390 runs past the 1368-residue target |
| `P53_HUMAN_Giacomelli_2018_Null_Etoposide`, `_Null_Nutlin`, `_WT_Nutlin` | The target has R72, but the shared `P53_HUMAN.pdb` has P72 (the natural P72R polymorphism) |

## Decision

Keep an explicit `structure_exceptions` mapping in `conf/data/proteingym.yaml` (assay id → reason).
These assays:

- are reported by `check-structures` as **allowed exceptions** (logged with both the documented
  reason and the failure actually observed) instead of as failures;
- get **no structure mapping**, so structure-based models never see them;
- stay in the dataset for sequence-only models.

The list must describe the data exactly. The check fails if an entry stops failing, which means
the upstream data was fixed, or if it names an assay that is not in the reference file.

We rejected three alternatives:

- **Silently dropping** the assays breaks CLAUDE.md data rule 3. It would also shrink the
  benchmark without any record, which makes our averages hard to compare with ProteinGym's.
- **"Fixing" the data**, by trimming the CAS9 tag or mutating residue 72 in the PDB, would
  mean altering published data and producing a structure that was never predicted.
- **Failing the whole pipeline** would block all work on 4 known and understood cases.

## Consequences

- Structure-based results cover 213 assays, while sequence-only results cover 217. Comparisons
  between the two must use the same 213-assay subset, and reports must state the assay count.
- Adding an exception requires a written reason in the config, which is reviewed like any other
  change.
- A future release that fixes these assays makes the check fail until the stale entries are
  removed, so the list cannot quietly fall out of date.
