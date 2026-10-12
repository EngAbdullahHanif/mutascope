"""Structure tokens: ``#`` below the pLDDT threshold and outside the structure, checks on input.

``processed_root`` holds assay ``A`` (target ``MKVAR``, 3Di ``DDVVL``, pLDDT 90, 85, 60, 95, 50)
and assay ``B`` without a structure.
"""

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from mutascope.data.schemas import StructureFeatureSchema, ThreeDiSchema
from mutascope.scoring.struct_tokens import (
    AssayStructure,
    StructTokenError,
    build_struct_tokens,
    check_struct_tokens,
    load_struct_tokens,
)
from mutascope.utils.io import write_partition


def test_low_plddt_and_residues_outside_the_structure_become_hash() -> None:
    """A structure of residues 2-5 of an 8-residue target. Exactly 70 keeps its letter (the
    rule is strictly below, as upstream), 69.99 does not; letters are lowercased."""
    tokens = build_struct_tokens(8, [2, 3, 4, 5], list("DVLA"), [70.0, 69.99, 95.0, 12.0], 70.0)
    assert tokens == "##d#l###"


def test_the_threshold_is_an_argument() -> None:
    assert build_struct_tokens(3, [0, 1, 2], list("DVL"), [40.0, 60.0, 80.0], 50.0) == "#vl"
    assert build_struct_tokens(3, [0, 1, 2], list("DVL"), [40.0, 60.0, 80.0], 0.0) == "dvl"


@pytest.mark.parametrize("positions", [[0, 3], [1, 1], [-1, 0]])
def test_positions_outside_the_target_or_repeated_fail(positions: list[int]) -> None:
    with pytest.raises(StructTokenError, match="outside the target or twice"):
        build_struct_tokens(3, positions, ["D", "V"], [90.0, 90.0], 70.0)


def test_check_rejects_wrong_length_and_letters() -> None:
    check_struct_tokens("dv#", 3)
    with pytest.raises(ValueError, match="2 structure tokens for 3 residues"):
        check_struct_tokens("dv", 3)
    with pytest.raises(ValueError, match=r"found \['D', 'b'\]"):
        check_struct_tokens("Db#", 3)  # uppercase, and b is not a 3Di state


def test_loads_the_assays_that_have_a_structure(processed_root: Path) -> None:
    processed = processed_root / "processed"
    structures = load_struct_tokens(
        processed / "three_di",
        processed / "structure_features",
        {"A": "MKVAR", "B": "MKVAR"},
        70.0,
    )
    assert structures == {"A": AssayStructure("A", "dd#v#", 0, 5)}
    assert structures["A"].low_plddt == 2


def _write(root: Path, name: str, frame: pd.DataFrame) -> Path:
    schema = ThreeDiSchema if name == "three_di" else StructureFeatureSchema
    (root / name).mkdir(parents=True)
    with duckdb.connect() as con:
        write_partition(con, schema.validate(frame), root / name / "assay_id=P")
    return root / name


def _tables(tmp_path: Path, residues: str, first: int) -> tuple[Path, Path]:
    positions = list(range(first, first + len(residues)))
    base = pd.DataFrame({"assay_id": "P", "position": positions, "residue": list(residues)})
    three_di = _write(tmp_path, "three_di", base.assign(three_di=["D"] * len(residues)))
    features = _write(
        tmp_path,
        "structure_features",
        base.assign(plddt=90.0, sasa=10.0, rel_sasa=0.1, contact_count=3),
    )
    return three_di, features


def test_a_partial_structure_is_placed_at_its_target_positions(tmp_path: Path) -> None:
    three_di, features = _tables(tmp_path, "VAR", first=2)
    structure = load_struct_tokens(three_di, features, {"P": "MKVARLL"}, 70.0)["P"]
    assert (structure.tokens, structure.start, structure.end) == ("##ddd##", 2, 5)


def test_a_structure_that_disagrees_with_the_target_fails(tmp_path: Path) -> None:
    three_di, features = _tables(tmp_path, "VAR", first=2)
    with pytest.raises(StructTokenError, match="residues 3-5 do not match the target"):
        load_struct_tokens(three_di, features, {"P": "MKVAGLL"}, 70.0)
    with pytest.raises(StructTokenError, match="do not match the target"):
        load_struct_tokens(three_di, features, {"P": "MKVA"}, 70.0)  # runs past the target


def test_tables_covering_different_residues_fail(tmp_path: Path) -> None:
    three_di, _ = _tables(tmp_path, "VAR", first=2)
    _, features = _tables(tmp_path / "other", "VA", first=2)
    with pytest.raises(StructTokenError, match="cover different residues"):
        load_struct_tokens(three_di, features, {"P": "MKVARLL"}, 70.0)
