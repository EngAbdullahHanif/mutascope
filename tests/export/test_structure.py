"""Structure files for the bundle (ADR 0039): backbone parsing, the residue check, encoding."""

import hashlib
import zipfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from tests.synthetic_pdb import atom_line, backbone_xyz, make_pdb

from mutascope.export.bundle import encode, load_structure
from mutascope.export.format import dequantize
from mutascope.export.structure import (
    StructureError,
    check_against_target,
    check_plddt,
    parse_backbone,
    read_structures_zip,
    structure_file,
    structure_path,
)

SEQUENCE = "MKVGRL"
PLDDT = [91.23, 88.0, 75.5, 60.25, 99.9, 70.0]


def test_keeps_backbone_atoms_residue_numbers_and_plddt_only() -> None:
    backbone = parse_backbone("X.pdb", make_pdb(SEQUENCE, PLDDT, first_number=11))
    assert backbone.sequence == SEQUENCE
    assert backbone.residue_numbers == list(range(11, 17))
    assert backbone.chain == "A"
    assert backbone.coordinates.shape == (6, 4, 3)  # N, CA, C, O: no H, CB or OXT
    assert tuple(backbone.coordinates[3, 1]) == pytest.approx(backbone_xyz(3, 1))
    assert backbone.plddt.tolist() == PLDDT


def test_structure_file_decodes_to_the_pdb_values_exactly() -> None:
    backbone = parse_backbone("X.pdb", make_pdb(SEQUENCE, PLDDT))
    encoded = structure_file(backbone, plddt_decimals=2)
    assert encoded.atoms == ("N", "CA", "C", "O")
    assert encoded.coordinates.decimals == 3
    assert np.array_equal(dequantize(encoded.coordinates), backbone.coordinates.ravel())
    assert dequantize(encoded.plddt).tolist() == PLDDT
    assert load_structure(encode(encoded)) == encoded
    assert encoded.source_sha256 == hashlib.sha256(make_pdb(SEQUENCE, PLDDT).encode()).hexdigest()


def test_a_full_structure_matches_its_target() -> None:
    backbone = parse_backbone("X.pdb", make_pdb(SEQUENCE, PLDDT))
    check_against_target("A1", backbone, SEQUENCE, 0, 6)


def test_a_partial_structure_maps_from_its_offset() -> None:
    # pdb_range 3-8 of a 10-residue target: structure residue 0 is target position 2.
    backbone = parse_backbone("X.pdb", make_pdb(SEQUENCE, PLDDT))
    check_against_target("A1", backbone, "AA" + SEQUENCE + "WW", 2, 8)
    # Off by one (the 1-indexed start used as a 0-indexed one) is caught at the first residue.
    with pytest.raises(StructureError, match=r"residue 0 .* is M, target position 3 is K"):
        check_against_target("A1", backbone, "AA" + SEQUENCE + "WW", 3, 9)


def test_a_residue_mismatch_fails_loudly_naming_assay_residue_and_count() -> None:
    backbone = parse_backbone("X.pdb", make_pdb(SEQUENCE, PLDDT, first_number=5))
    target = "MKVPRA"  # G -> P at position 3, L -> A at 5
    with pytest.raises(
        StructureError,
        match=r"A1: X\.pdb residue 3 \(PDB number 8\) is G, target position 3 is P "
        r"\(2 of 6 residues differ\)",
    ):
        check_against_target("A1", backbone, target, 0, 6)


def test_a_structure_of_another_length_than_its_range_fails() -> None:
    backbone = parse_backbone("X.pdb", make_pdb(SEQUENCE, PLDDT))
    with pytest.raises(StructureError, match="has 6 residues"):
        check_against_target("A1", backbone, SEQUENCE + "A", 0, 7)


def test_plddt_must_agree_with_the_structure_features() -> None:
    backbone = parse_backbone("X.pdb", make_pdb(SEQUENCE, PLDDT))
    features = {2 + i: v for i, v in enumerate([91.234, 88.0, 75.5, 60.25, 99.9, 70.0])}
    check_plddt("A1", backbone, features, 2)
    with pytest.raises(StructureError, match=r"target position 4 is 75\.5"):
        check_plddt("A1", backbone, features | {4: 74.0}, 2)
    with pytest.raises(StructureError, match="other residues"):
        check_plddt("A1", backbone, features, 1)


def _replace_line(text: str, index: int, line: str) -> str:
    lines = text.split("\n")
    lines[index] = line
    return "\n".join(lines)


MALFORMED: list[tuple[Callable[[str], str], str]] = [
    (lambda t: t.replace(" CA  MET", " CA BMET"), "alternate location"),
    (lambda t: t.replace(" A   2", " B   2"), "one chain"),
    (lambda t: t.replace("MET", "MSE"), "non-canonical"),
    (lambda t: "\n".join(x for x in t.split("\n") if " O   LYS" not in x), "has no O"),
    (
        lambda t: _replace_line(t, 2, atom_line(3, "CA", "MET", "A", 1, (0.0, 0.0, 0.0), 50.0)),
        "several B-factors",
    ),
    (lambda t: t.replace("LEU A   6", "LEU A   1"), "appears again"),
    (
        lambda t: t.replace("LEU A   6", "LEU A   7").replace("ARG A   5", "ARG A   9"),
        "strictly increasing",
    ),
    (lambda t: "", "no ATOM"),
]


@pytest.mark.parametrize(("edit", "message"), MALFORMED)
def test_malformed_pdbs_fail(edit: Callable[[str], str], message: str) -> None:
    text = edit(make_pdb(SEQUENCE, PLDDT))
    with pytest.raises(StructureError, match=message):
        parse_backbone("X.pdb", text)


def test_the_archive_is_read_only_with_its_manifest_checksum(tmp_path: Path) -> None:
    path = tmp_path / "ProteinGym_AF2_structures.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ProteinGym_AF2_structures/X.pdb", make_pdb(SEQUENCE, PLDDT))
        archive.writestr("ProteinGym_AF2_structures/.DS_Store", "junk")
        archive.writestr("__MACOSX/ProteinGym_AF2_structures/._X.pdb", "junk")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    structures = read_structures_zip(path, digest)
    assert list(structures) == ["X.pdb"]
    with pytest.raises(StructureError, match="differs from the manifest"):
        read_structures_zip(path, "0" * 64)


def test_structure_files_are_named_after_their_pdb() -> None:
    assert structure_path("A0A140D2T1_ZIKV.pdb") == "structures/A0A140D2T1_ZIKV.json.gz"
