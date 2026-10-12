# MutaScope: ProteinGym v1.3 single mutants

Every single amino-acid substitution measured in the 217 deep mutational scanning (DMS) assays of
[ProteinGym](https://proteingym.org) v1.3, cleaned and validated, plus per-residue structure
features and Foldseek 3Di tokens for the assays' AlphaFold2 structures. It is the processed data
behind [MutaScope](https://github.com/EngAbdullahHanif/mutascope), a project that predicts and
benchmarks the functional effect of protein mutations.

Scores are **functional effects measured in laboratory DMS assays** (growth, binding, stability,
activity, expression). They are not clinical pathogenicity labels and must not be used as such.

## At a glance

| | |
|---|---:|
| Assays | 217 |
| Proteins (distinct UniProt IDs) | 186 |
| Single-mutant variants | 696,311 |
| Assays with structure features and 3Di | 213 |
| Residues with structure features | 77,338 |
| Target sequence length | 37 to 3,423 (median 245) |
| Single mutants per assay | 33 to 16,897 (median 1,340) |

| Function type (`coarse_selection_type`) | Assays | Proteins | Single mutants |
|---|---:|---:|---:|
| OrganismalFitness | 77 | 64 | 375,411 |
| Stability | 66 | 66 | 68,855 |
| Activity | 43 | 40 | 145,367 |
| Expression | 18 | 18 | 74,923 |
| Binding | 13 | 12 | 31,755 |

The proteins come from humans (96 assays), prokaryotes (50), other eukaryotes (40) and viruses (31).

## Source and version

- **Release:** ProteinGym v1.3, Zenodo [10.5281/zenodo.15293562](https://doi.org/10.5281/zenodo.15293562)
  (published 2025-04-27), git tag `PG_v1.3` = commit `1f8de974dead8ff7501eff087b725d14a965e9f9`
  of [OATML-Markslab/ProteinGym](https://github.com/OATML-Markslab/ProteinGym).
- **Files used:** `DMS_substitutions.csv` (one row per assay), `DMS_ProteinGym_substitutions.zip`
  (the measurements) and `ProteinGym_AF2_structures.zip` (AlphaFold2 models matched to each
  assay's target sequence; not AlphaFold DB entries).
- **Integrity:** each file was checked against the md5 published on Zenodo on first download; its
  sha256 is committed in MutaScope's
  [`data/manifest.yaml`](https://github.com/EngAbdullahHanif/mutascope/blob/main/data/manifest.yaml)
  and verified on every later run. The same checksums are in this dataset's `provenance.json`.
- **Underlying measurements** come from the original publications of each assay (author, year
  and journal are in the `assays` table). ProteinGym standardised them; we did not re-derive them.

## Contents

Four tables, one Parquet file each. `assay_id` is ProteinGym's `DMS_id` and joins every table.
Positions are **0-based** indices into the assay's `target_seq`; mutation strings keep
ProteinGym's 1-based numbering (`A123G` is `position` 122).

**`variants`** (default config): one row per (assay, single mutant), 696,311 rows.

| Column | Type | Meaning |
|---|---|---|
| `assay_id` | string | ProteinGym assay id |
| `mutant` | string | Substitution, 1-based, e.g. `H24C` |
| `position` | int | 0-based position in `target_seq` |
| `wt_aa`, `mt_aa` | string | Wild-type and mutant residue (20 canonical amino acids) |
| `DMS_score` | float | Measured effect, as published by ProteinGym. **Higher = fitter.** Units differ per assay |
| `DMS_score_bin` | int | ProteinGym's binary label: 1 = fit, 0 = not fit (see limitations) |

**`assays`**: one row per assay, 217 rows. A subset of ProteinGym's reference file: `UniProt_ID`,
`taxon`, `source_organism`, `molecule_name`, `target_seq`, `seq_len`, `coarse_selection_type`
(function type), `selection_assay`, `selection_type`, `DMS_number_single_mutants`,
`DMS_binarization_cutoff`, `DMS_binarization_method` (`median` or `manual`), `first_author`,
`year`, `jo` (journal), `pdb_file`, `pdb_range` (1-based, inclusive), plus `has_structure`
(false for the 4 assays without structure features, below).

**`structure_features`**: one row per (assay, residue covered by its structure), 77,338 rows.

| Column | Meaning |
|---|---|
| `position`, `residue` | 0-based position and its residue in `target_seq` |
| `plddt` | AlphaFold2 confidence, 0 to 100 |
| `sasa` | Solvent-accessible surface area of the residue's heavy atoms, Å² (Shrake-Rupley, 1.4 Å probe) |
| `rel_sasa` | `sasa` over the residue's theoretical maximum; not clipped, so exposed termini can exceed 1 |
| `contact_count` | Other residues whose Cβ (Cα for glycine) is within 8 Å of this residue's Cβ, in the whole chain |

**`three_di`**: one row per (assay, residue), 77,338 rows: `position`, `residue` and `three_di`,
the Foldseek 3Di structural state (20 letters), from Foldseek 10-941cd33.

Also included: `reports/` (per-assay cleaning and structure reports, Parquet and Markdown),
`provenance.json` (release pin, input checksums, Foldseek version, row counts) and `SHA256SUMS`.

## How it was built

All steps are commands of the `mutascope` CLI, chained by a Nextflow pipeline (`make pipeline`).
The rules are recorded as architecture decision records in the
[repository](https://github.com/EngAbdullahHanif/mutascope/tree/main/docs/adr).

1. **Validate** (ADR 0004). Every one of the 2,465,767 raw rows is checked three ways: the row
   matches a schema, the stated wild-type residue is at that position of `target_seq`, and
   `mutated_sequence` equals `target_seq` with the substitution applied. All rows passed. A
   mismatch would stop the run with its assay and row; nothing is ever silently fixed or dropped.
2. **Clean** (ADR 0005). Each removed row is counted under exactly one reason, the first that
   applies:

   | Reason | Rows removed |
   |---|---:|
   | Multi-mutant (v1 keeps single substitutions only), in 69 assays | 1,769,456 |
   | Missing `mutant`, `mutated_sequence`, `DMS_score` or `DMS_score_bin` | 0 |
   | Exact duplicate (first copy kept) | 0 |
   | Same mutant with conflicting values (no copy kept) | 0 |
   | **Single mutants kept** | **696,311** |

   Scores are parsed with correctly rounded decimal conversion, so every stored `DMS_score` equals
   the published text exactly. `DMS_score` is never sign-flipped. Per assay, the kept count equals
   ProteinGym's `DMS_number_single_mutants`; the publish step re-checks this.
3. **Structures** (ADRs 0003, 0006). Each assay's AF2 model is matched to its `target_seq` over
   `pdb_range` and checked residue by residue. 213 assays (185 PDB files) match. Four do not,
   because of upstream data, and get no structure rows; they stay in `variants`:
   - `CAS9_STRP1_Spencer_2017_positive`: the PDB carries a 22-residue C-terminal tag, so
     `pdb_range` 1-1390 runs past the 1,368-residue target.
   - `P53_HUMAN_Giacomelli_2018_Null_Etoposide`, `_Null_Nutlin`, `_WT_Nutlin`: the target has R72,
     the shared PDB the natural P72 variant.

   For the 213 matched assays, every variant falls at a position that has structure features
   (665,793 of 665,793). Secondary structure is deliberately not included (ADR 0006).
4. **Write**. Every table is validated against a pandera schema before it is written. Staging the
   Hub upload twice produces byte-identical files.

## Intended use

- Benchmarking and training variant-effect predictors on single substitutions, following
  ProteinGym's protocols.
- Exploring mutational landscapes alongside structure (pLDDT, burial, contacts, 3Di).
- Structure-aware models such as SaProt, which take the 3Di tokens directly (mask 3Di where
  `plddt < 70`, as SaProt does).

**Out of scope:** clinical interpretation of human variants; multi-mutant or indel effects; any
comparison of raw `DMS_score` values across assays.

## Known limitations

- **Scores are not comparable across assays.** Each assay has its own units and dynamic range
  (enrichment ratios, ΔΔG, growth rates). Standardise within an assay or use rank metrics.
  ProteinGym reports Spearman per assay, averaged per protein, then per function type.
- **Assay sizes vary 500-fold** (33 to 16,897 single mutants), so per-assay metrics are noisy for
  small assays. 5 assays have fewer than 100 single mutants.
- **Coverage is uneven.** Targets of up to 1,022 residues have a median of 90% of all 19 × L possible
  substitutions measured; the 16 longer targets have a median of 4.6%. Those 16 also exceed
  ESM-2's 1,022-residue context and need sliding windows.
- **One study dominates stability.** 64 of the 66 stability assays come from a single
  high-throughput study (Tsuboyama et al., 2023) of small domains of 37 to 72 residues. Stability
  results mostly reflect that method and protein size range.
- **Binary labels inherit ProteinGym's cutoffs**, set per assay either at the median score (126
  assays) or manually (91). Where the median was taken over multi-mutants as well, the single
  mutants, which are usually fitter, are skewed towards label 1. Of the 43 median-binarized assays
  with multi-mutants, 32 have a share of single mutants labelled 1 more than 10 points away from
  50% (median share 67%). The 83 without multi-mutants range from 45% to 53%. In
  `F7YBW8_MESOW_Aakre_2015` the cutoff (−0.0017) is the median over 9,192 rows, 9,155 of them
  multi-mutants, and all 37 single mutants lie above it, so every label is 1. AUC and MCC are
  undefined for that assay.
- **Structures are predictions, not experiments.** 23% of residues with structure features have
  pLDDT below 70, and 18 of the 213 structures have a mean pLDDT below 70 (lowest:
  `BRCA1_HUMAN_Findlay_2018`, 41). Treat SASA, contacts and 3Di in those regions with caution.
  Three structures cover only part of their target (`A0A140D2T1_ZIKV_Sourisseau_2019`,
  `BRCA2_HUMAN_Erwood_2022_HEK293T`, `POLG_HCVJF_Qi_2014`), though all of their measured variants
  fall inside the modelled range.
- **Four assays have no structure features** (above), so structure-based models are evaluated on
  213 assays and sequence-only models on 217. Results should say which.
- **Assays share proteins.** 217 assays cover 186 UniProt IDs (up to 4 assays per protein), so
  splitting by assay does not separate proteins.
- **Single substitutions only.** Multi-mutants, insertions and deletions are not included.
- **Organism and selection bias.** 96 of 217 assays are of human proteins, and the set reflects
  which proteins and functions DMS studies chose to measure. It is not a random sample of
  proteins or functions.

## Licence and citation

ProteinGym v1.3 is released under the MIT licence (Zenodo record and GitHub repository), and this
derived dataset is released under the same **MIT licence**. The underlying measurements come
from the cited original studies; please cite them as well when you use specific assays.

If you use this dataset, cite ProteinGym:

```bibtex
@inproceedings{notin2023proteingym,
  author    = {Notin, Pascal and Kollasch, Aaron and Ritter, Daniel and van Niekerk, Lood and
               Paul, Steffanie and Spinner, Han and Rollins, Nathan and Shaw, Ada and
               Orenbuch, Rose and Weitzman, Ruben and Frazer, Jonathan and Dias, Mafalda and
               Franceschi, Dinko and Gal, Yarin and Marks, Debora},
  title     = {ProteinGym: Large-Scale Benchmarks for Protein Fitness Prediction and Design},
  booktitle = {Advances in Neural Information Processing Systems},
  volume    = {36},
  pages     = {64331--64379},
  year      = {2023}
}
```

and, for the 3Di tokens, Foldseek (van Kempen et al., *Nature Biotechnology* 2024).

## Loading

```python
from datasets import load_dataset

variants = load_dataset("engAbdullahHanif/mutascope-proteingym-v1.3", "variants", split="train")
structure = load_dataset(
    "engAbdullahHanif/mutascope-proteingym-v1.3", "structure_features", split="train"
)
```

```sql
-- DuckDB, straight from the Hub
SELECT v.*, s.plddt, s.rel_sasa
FROM 'hf://datasets/engAbdullahHanif/mutascope-proteingym-v1.3/data/variants.parquet' v
LEFT JOIN 'hf://datasets/engAbdullahHanif/mutascope-proteingym-v1.3/data/structure_features.parquet' s
  USING (assay_id, position);
```

## Reproducing

```bash
git clone https://github.com/EngAbdullahHanif/mutascope && cd mutascope
make setup
make pipeline                # download, verify, validate, clean, structure features, 3Di
uv run mutascope data publish  # stage this dataset in build/hf_dataset (no upload)
```

`provenance.json` records the inputs and versions; `SHA256SUMS` lets you compare a rebuilt copy
file by file.
