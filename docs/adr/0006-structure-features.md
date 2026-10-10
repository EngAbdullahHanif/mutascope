# 0006 — Per-residue structure features and Foldseek 3Di

- Status: accepted
- Date: 2026-10-10

## Context

Phase 3 models use structure features per variant position, and SaProt (Phase 2) needs a 3Di
token per residue. Both come from ProteinGym's AF2 structures, mapped to assays as described in
ADR 0003 (213 of 217 assays; 4 documented exceptions). Several assays share one PDB (185 PDBs
for the 213 assays), and 4 PDBs model only a window of `target_seq` (`pdb_range`).

## Decision

**Keys.** Two tables, `structure_features` and `three_di`, one row per (assay, residue), keyed by
`position`, the 0-based `target_seq` index used by the variants table. Structure residue `i` is
at `StructureMapping.offset + i`, where `offset = range_start - 1` is the only place a
`pdb_range` becomes a 0-based index. Each table is Parquet partitioned by `assay_id`, with a
pandera schema that requires positions to be contiguous and unique, and is built in a `.tmp`
directory that replaces the old one only after every assay passes. The structure exceptions get
no rows. `StructureCheck` refuses to hold an assay that is both mapped and an exception, and
the report fails if either table has a partition for one.

**Sequence checks.** A structure must match its target range in three independent parsers: the
CA-line reader in `check-structures`, biotite, and Foldseek's own amino-acid output. The report
also checks that the two tables have identical keys and residues.

**Features** (computed once per PDB, then laid onto each assay):

| column | definition | why |
|---|---|---|
| `plddt` | B-factor, required to be equal on all atoms of a residue | proves the column is per-residue pLDDT |
| `sasa`, `rel_sasa` | biotite Shrake-Rupley, ProtOr radii, probe 1.4 Å, 1,000 points, on heavy atoms only; divided by Tien et al. (2013) theoretical max ASA | ProtOr radii are united-atom radii that already include H, and the PDBs carry explicit H; `rel_sasa` is not clipped |
| `sse` | P-SEA via `biotite.structure.annotate_sse`: `a`/`b`/`c` | no external binary (DSSP); see consequences |
| `contact_count` | other residues with CB (CA for Gly) closer than 8 Å | CASP contact definition; includes sequence neighbours |

Parameters live in `conf/structure/default.yaml`, not in code.

**3Di.** `foldseek structureto3didescriptor --chain-name-mode 1`, run once on all mapped PDBs,
parsed as in the official SaProt repository (`utils/foldseek_util.py`). A PDB's TITLE is
appended to its entry name after a space, so only the first word is used. 3Di strings depend on
the Foldseek build, so `structure.foldseek.version` pins the commit printed by
`foldseek version` (GitHub release `10-941cd33`), and any other binary is refused. Foldseek is
not in Homebrew core and conda is not used here, so it is installed from the official release
tarball:

```bash
curl -LO https://github.com/steineggerlab/foldseek/releases/download/10-941cd33/foldseek-osx-universal.tar.gz
# sha256 6d8b07e188d443044f0c98db30aa3d4cca6750e97b50679372b54b45a664cae5
tar xzf foldseek-osx-universal.tar.gz && cp foldseek/bin/foldseek ~/.local/bin/
export FOLDSEEK_BIN=~/.local/bin/foldseek   # if not on PATH
```

The same release ships `foldseek-linux-avx2.tar.gz` for the amd64 Docker image. SaProt's `#`
masking of residues with pLDDT < 70 belongs to its tokeniser, so it is applied by the scorer from
the `plddt` column, and the stored 3Di stay unmasked.

**Report.** `mutascope data structure-report` writes `results/data/structure_report.{parquet,md}`
with one row per assay, giving coverage, pLDDT, SSE fractions, and the documented and observed
reason for each exception.

## Alternatives rejected

- **DSSP** (`mkdssp`, via `biotite.application.dssp`) assigns SSE from hydrogen bonds, which is
  the standard. It adds a second external binary, and the roadmap specified biotite. This
  choice is open for review; see consequences.
- **Keying by PDB instead of assay**: downstream joins are per assay, and windowed PDBs need
  each assay's offset. Duplicating the shared PDBs costs 2.4 MB.
- **Storing 3Di as one string per assay**: per-residue rows join with variants and pLDDT
  directly, and the string is `string_agg(three_di ORDER BY position)`.

## Consequences

- On v1.3: 213 assays, 185 PDBs and 77,338 residue rows in each table, in about 11 s
  (features) and 1 s (3Di). Every variant in a mapped assay (665,793) has structure features:
  ProteinGym's windows cover the assayed regions.
- **P-SEA calls disordered AF2 regions strand.** It uses CA geometry only, so the extended
  "spaghetti" AF2 produces for disordered regions passes as β-strand. Among residues with
  pLDDT < 50, 32.9% are called strand, against 18.4% at pLDDT ≥ 90. Models get `plddt` next to
  `sse`, but a switch to DSSP may be warranted before Phase 3.
- Docker images will need the linux Foldseek binary from the same release.
