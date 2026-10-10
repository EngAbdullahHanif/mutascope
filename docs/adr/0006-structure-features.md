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
| `plddt` | B-factor, required to be equal on all atoms of a residue | proves the column is per-residue pLDDT; also the flag for disordered regions |
| `sasa`, `rel_sasa` | biotite Shrake-Rupley, ProtOr radii, probe 1.4 Å, 1,000 points, on heavy atoms only; divided by Tien et al. (2013) theoretical max ASA | ProtOr radii are united-atom radii that already include H, and the PDBs carry explicit H; `rel_sasa` is not clipped |
| `contact_count` | other residues with CB (CA for Gly) closer than 8 Å | CASP contact definition; includes sequence neighbours |

Parameters live in `conf/structure/default.yaml`, not in code.

**No secondary structure.** The table deliberately has no SSE column; see "Alternatives rejected".
Disordered regions are identified by low `plddt`.

**3Di.** `foldseek structureto3didescriptor --chain-name-mode 1`, run once on all mapped PDBs,
parsed as in the official SaProt repository (`utils/foldseek_util.py`). A PDB's TITLE is
appended to its entry name after a space, so only the first word is used. 3Di strings depend on
the Foldseek build, so `structure.foldseek.version` pins the commit printed by
`foldseek version` (GitHub release `10-941cd33`), and any other binary is refused.
SaProt's `#` masking of residues with pLDDT < 70 belongs to its tokeniser, so it is applied by
the scorer from the `plddt` column, and the stored 3Di stay unmasked.

**Finding and recording Foldseek.** The binary is not a Python dependency, so it is never
assumed to be somewhere in particular. `foldseek-3di` resolves it before doing any work:
`FOLDSEEK_BIN` if set (it must be an executable file), otherwise `foldseek` on `PATH`. If
neither works, the command fails with install instructions (README, "Installing Foldseek").
The binary's version, sha256 and how it was found (`FOLDSEEK_BIN` or `PATH`) are written to
`three_di/foldseek_run.json`, together with its absolute path, which is kept for local debugging
(the file lives under the gitignored `data/`). `structure-report` requires that file and prints
the version, sha256 and lookup but not the path, so every 3Di table names the exact executable
that produced it, and the committed report stays machine-independent. Homebrew has no `foldseek`
formula and conda is not used here, so the documented install is the official release tarball
(sha256 `6d8b07e1…cae5`). The same release has `foldseek-linux-avx2` for CI and the amd64
image.

**Report.** `mutascope data structure-report` writes `results/data/structure_report.{parquet,md}`
with one row per assay (coverage, pLDDT, SASA, contacts, and the documented and observed reason
for each exception), plus the Foldseek binary section. It has no timestamps or local paths,
so a rerun with the same data and Foldseek build is byte-identical on any machine.

## Alternatives rejected

- **P-SEA** (`biotite.structure.annotate_sse`, the first implementation). It assigns states
  from CA geometry alone, so the extended "spaghetti" AF2 produces for disordered regions
  passes as β-strand. On v1.3, 32.9% of the 12,301 residues with pLDDT < 50 were called strand,
  against 18.4% of the 40,323 with pLDDT ≥ 90 (BRCA1, mean pLDDT 41, came out 35% strand).
  That is known-wrong data, and keeping it with a caveat was rejected: a model would learn
  "strand" as a proxy for disorder.
- **DSSP** (`mkdssp`, hydrogen-bond based, the standard). It is not installable portably:
  Homebrew has no `dssp` formula, conda is not used, and PyPI has no official bindings
  (`mkdssp` and `dssp` do not exist there). Building DSSP 4 from source (cmake, libcifpp) would
  have to be repeated in CI and the amd64 image, which is the fragility the Foldseek pin exists
  to avoid. `pydssp` (PyPI) is a simplified reimplementation rather than DSSP, so swapping it in
  would mean another unvalidated method. If SSE is needed later, it needs its own ADR and a
  validated, portable DSSP.
- **Keying by PDB instead of assay**: downstream joins are per assay, and windowed PDBs need
  each assay's offset. Duplicating the shared PDBs costs 2.4 MB.
- **Storing 3Di as one string per assay**: per-residue rows join with variants and pLDDT
  directly, and the string is `string_agg(three_di ORDER BY position)`.

## Consequences

- On v1.3: 213 assays, 185 PDBs and 77,338 residue rows in each table, in about 11 s
  (features) and 1 s (3Di). Every variant in a mapped assay (665,793) has structure features:
  ProteinGym's windows cover the assayed regions.
- Phase 3 structure features are pLDDT, relative SASA and contact count, with no SSE.
- CI and Docker must install Foldseek `10-941cd33` (linux-avx2) and put it on `PATH` or set
  `FOLDSEEK_BIN`. Until then, the real-Foldseek golden test is skipped there.
