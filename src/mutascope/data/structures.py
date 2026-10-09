"""Map each DMS assay to its ProteinGym AF2 structure, and check the mapping really holds.

The reference file gives, per assay, ``pdb_file`` and ``pdb_range`` ("start-end", 1-based and
inclusive, as published): the PDB models residues ``start..end`` of ``target_seq``. Most PDBs
cover the whole target; a few long proteins (e.g. viral polyproteins) only a window. A mapping is
accepted only if the PDB's residue sequence is identical to that slice of ``target_seq``, so a
structure can never be silently paired with a different sequence.
"""

import csv
from dataclasses import dataclass
from pathlib import Path

from mutascope.utils.logging import get_logger

log = get_logger(__name__)

THREE_TO_ONE = {
    "ALA": "A", "CYS": "C", "ASP": "D", "GLU": "E", "PHE": "F",
    "GLY": "G", "HIS": "H", "ILE": "I", "LYS": "K", "LEU": "L",
    "MET": "M", "ASN": "N", "PRO": "P", "GLN": "Q", "ARG": "R",
    "SER": "S", "THR": "T", "VAL": "V", "TRP": "W", "TYR": "Y",
}  # fmt: skip


class StructureMappingError(ValueError):
    """One or more assays could not be mapped to a matching structure."""


@dataclass(frozen=True)
class StructureMapping:
    """An assay's structure and the 1-based inclusive ``target_seq`` range it models."""

    assay_id: str
    pdb_path: Path
    range_start: int
    range_end: int


def pdb_sequence(pdb_text: str) -> str:
    """One-letter sequence of a single-chain PDB, read from its CA atoms.

    Raises:
        ValueError: The file has no CA atoms, several chains, or a non-canonical residue.
    """
    residues: list[str] = []
    chains: set[str] = set()
    for line in pdb_text.splitlines():
        if line.startswith("ATOM") and line[12:16].strip() == "CA":
            chains.add(line[21])
            resname = line[17:20]
            if resname not in THREE_TO_ONE:
                raise ValueError(f"non-canonical residue {resname!r}")
            residues.append(THREE_TO_ONE[resname])
    if not residues:
        raise ValueError("no CA atoms")
    if len(chains) > 1:
        raise ValueError(f"expected one chain, found {sorted(chains)}")
    return "".join(residues)


def parse_pdb_range(text: str) -> tuple[int, int]:
    """Parse ``"start-end"`` into ints (still 1-based inclusive)."""
    start, sep, end = text.strip().partition("-")
    if not sep or not start.isdigit() or not end.isdigit():
        raise ValueError(f"malformed pdb_range {text!r}")
    return int(start), int(end)


def map_structures(reference_csv: Path, structures_dir: Path) -> list[StructureMapping]:
    """Map every assay in the reference file to its structure, or fail naming every bad assay.

    All assays are checked before raising, so one run reports the complete list of problems.

    Raises:
        StructureMappingError: At least one assay is unmapped; the message lists each one.
    """
    with reference_csv.open(newline="") as fh:
        rows = list(csv.DictReader(fh))

    mappings: list[StructureMapping] = []
    failures: list[str] = []
    sequences: dict[str, str] = {}  # PDBs are shared across assays of the same protein
    for row_number, row in enumerate(rows, start=2):  # row 1 is the header
        assay_id = row["DMS_id"]
        try:
            mappings.append(_map_one(row, structures_dir, sequences))
        except (ValueError, OSError) as exc:
            log.error("structure_unmapped", assay_id=assay_id, row=row_number, reason=str(exc))
            failures.append(f"{assay_id} (row {row_number}): {exc}")

    if failures:
        raise StructureMappingError(
            f"{len(failures)} of {len(rows)} assays could not be mapped to a structure:\n  "
            + "\n  ".join(failures)
        )
    log.info("structures_mapped", assays=len(mappings), pdb_files=len(sequences))
    return mappings


def _map_one(
    row: dict[str, str], structures_dir: Path, sequences: dict[str, str]
) -> StructureMapping:
    pdb_file = row["pdb_file"].strip()
    if not pdb_file:
        raise ValueError("no pdb_file in reference")
    pdb_path = structures_dir / pdb_file
    if pdb_file not in sequences:
        if not pdb_path.is_file():
            raise ValueError(f"{pdb_file} not found in {structures_dir}")
        try:
            sequences[pdb_file] = pdb_sequence(pdb_path.read_text())
        except ValueError as exc:
            raise ValueError(f"{pdb_file}: {exc}") from exc
    structure_seq = sequences[pdb_file]

    target = row["target_seq"]
    start, end = parse_pdb_range(row["pdb_range"])
    if not 1 <= start <= end <= len(target):
        raise ValueError(f"pdb_range {start}-{end} outside target_seq of length {len(target)}")
    expected = target[start - 1 : end]  # 1-based inclusive range -> Python slice
    if structure_seq != expected:
        raise ValueError(
            f"{pdb_file} sequence differs from target_seq {start}-{end}: "
            + _describe_difference(expected, structure_seq, first_position=start)
        )
    return StructureMapping(row["DMS_id"], pdb_path, start, end)


def _describe_difference(expected: str, actual: str, *, first_position: int) -> str:
    """Summarise a mismatch; positions use target_seq numbering (1-based), e.g. ``R72P``."""
    if len(expected) != len(actual):
        return f"structure has {len(actual)} residues, range has {len(expected)}"
    pairs = enumerate(zip(expected, actual, strict=True), start=first_position)
    diffs = [f"{e}{pos}{a}" for pos, (e, a) in pairs if e != a]
    shown = ", ".join(diffs[:5]) + (" ..." if len(diffs) > 5 else "")
    return f"{len(diffs)} residue difference(s) (target->structure): {shown}"
