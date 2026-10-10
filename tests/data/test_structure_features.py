"""Per-residue structure features: values checked against the raw PDB text, failures are loud."""

import re
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pandera.errors
import pytest

from mutascope.data.schemas import StructureFeatureSchema
from mutascope.data.structure_features import (
    MAX_ASA,
    FeatureParams,
    StructureFeatureError,
    assay_features,
    build_structure_features,
    contact_counts,
    load_chain,
    residue_features,
)
from mutascope.data.structures import (
    THREE_TO_ONE,
    AllowedException,
    StructureCheck,
    StructureMappingError,
)

FIXTURE = Path(__file__).parent / "fixtures" / "SR43C_ARATH.pdb"
PARAMS = FeatureParams(
    contact_cutoff=8.0, sasa_probe_radius=1.4, sasa_point_number=1000, low_plddt_threshold=70.0
)


def atom_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("ATOM")]


@dataclass
class TextResidue:
    name: str = ""
    plddt: float = float("nan")
    contact_atom: tuple[float, float, float] | None = None


def text_residues(text: str) -> list[TextResidue]:
    """Name, CA B-factor and CB (CA for GLY) coordinates per residue, parsed by hand."""
    residues: dict[int, TextResidue] = {}
    for line in atom_lines(text):
        res = residues.setdefault(int(line[22:26]), TextResidue(name=line[17:20]))
        atom = line[12:16].strip()
        if atom == "CA":
            res.plddt = float(line[60:66])
        if atom == ("CA" if res.name == "GLY" else "CB"):
            res.contact_atom = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
    return [residues[k] for k in sorted(residues)]


@pytest.fixture
def features() -> pd.DataFrame:
    return residue_features(load_chain(FIXTURE), PARAMS)


def test_contact_counts_exclude_self_and_use_strict_cutoff() -> None:
    # Points 5 Å apart on a line: neighbours at 5 Å count, at 10 Å they don't.
    line = np.array([[0.0, 0, 0], [5, 0, 0], [10, 0, 0], [15, 0, 0]])
    assert contact_counts(line, 8.0).tolist() == [1, 2, 2, 1]
    # Exactly at the cutoff is not "closer than" it.
    assert contact_counts(np.array([[0.0, 0, 0], [8, 0, 0]]), 8.0).tolist() == [0, 0]


def test_features_match_the_raw_pdb_text(features: pd.DataFrame) -> None:
    raw = text_residues(FIXTURE.read_text())
    assert len(features) == len(raw) == 48
    assert features["residue"].tolist() == [THREE_TO_ONE[r.name] for r in raw]
    assert features["plddt"].tolist() == [r.plddt for r in raw]
    coords = np.array([r.contact_atom for r in raw], dtype=np.float64)
    distances = np.linalg.norm(coords[:, None] - coords[None, :], axis=-1)
    expected = [int((row < 8.0).sum()) - 1 for row in distances]
    assert features["contact_count"].tolist() == expected


def test_rel_sasa_is_sasa_over_tien_maximum(features: pd.DataFrame) -> None:
    expected = features["sasa"] / features["residue"].map(MAX_ASA)
    assert np.allclose(features["rel_sasa"], expected)
    # A buried residue has fewer exposed Å² and more contacts: the two must anticorrelate.
    assert np.corrcoef(features["rel_sasa"], features["contact_count"])[0, 1] < -0.5


def test_golden_values_for_fixture(features: pd.DataFrame) -> None:
    """Pinned outputs on a real structure; a change means the algorithm or a dependency moved."""
    assert "".join(features["sse"]) == "ccccccccccccccccbbbbbcccccccbbbbbccccccaaaaaaaac"
    assert features["contact_count"].tolist()[:8] == [6, 6, 12, 7, 9, 14, 8, 7]
    assert round(float(features["sasa"].sum()), 1) == 3556.3


def test_hydrogens_are_removed() -> None:
    chain = load_chain(FIXTURE)
    assert not any(name.startswith("H") for name in chain.atom_name)
    assert len(chain.atom_name) < len(atom_lines((FIXTURE).read_text()))


def write_variant(tmp_path: Path, edit: str) -> Path:
    """The fixture with one deliberate defect."""
    lines = (FIXTURE).read_text().splitlines()
    atoms = [i for i, line in enumerate(lines) if line.startswith("ATOM")]
    if edit == "bfactor":
        i = next(i for i in atoms if lines[i][12:16].strip() == "CB")
        lines[i] = lines[i][:60] + " 12.34" + lines[i][66:]
    elif edit == "no_cb":
        lines = [line for line in lines if not (line[12:16] == " CB " and line[22:26] == "   3")]
    elif edit == "gap":
        lines = [
            line[:22] + f"{int(line[22:26]) + 1:>4}" + line[26:]
            if line.startswith("ATOM") and int(line[22:26]) > 10
            else line
            for line in lines
        ]
    elif edit == "two_models":
        body = [line for line in lines if line.startswith("ATOM")]
        lines = ["MODEL        1", *body, "ENDMDL", "MODEL        2", *body, "ENDMDL", "END"]
    path = tmp_path / f"{edit}.pdb"
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        ("bfactor", "B-factor varies within 1 residue"),
        ("no_cb", "without exactly one CB"),
        ("gap", "not consecutive"),
        ("two_models", "expected 1 model, found 2"),
    ],
)
def test_defective_structures_fail_loudly(tmp_path: Path, edit: str, message: str) -> None:
    with pytest.raises(StructureFeatureError, match=re.escape(message)):
        residue_features(load_chain(write_variant(tmp_path, edit)), PARAMS)


def test_windowed_assay_is_placed_at_target_positions(
    check: StructureCheck, features: pd.DataFrame
) -> None:
    window = next(m for m in check.mappings if m.assay_id == "WINDOW")
    table = assay_features(window, features)
    assert window.offset == 3
    assert table["position"].tolist() == list(range(3, 3 + 48))
    assert "".join(table["residue"]) == window.target_seq[3:51]
    StructureFeatureSchema.validate(table)


def test_residue_mismatch_with_target_fails(check: StructureCheck, features: pd.DataFrame) -> None:
    full = next(m for m in check.mappings if m.assay_id == "FULL")
    shifted = features.copy()
    shifted["residue"] = shifted["residue"].shift(1, fill_value="M")
    with pytest.raises(StructureFeatureError, match="differ from target_seq"):
        assay_features(full, shifted)


def test_build_writes_mapped_assays_only(tmp_path: Path, check: StructureCheck) -> None:
    out = tmp_path / "features"
    rows = build_structure_features(check, out, PARAMS)

    assert sorted(p.name for p in out.iterdir()) == ["assay_id=FULL", "assay_id=WINDOW"]
    assert not (out / "assay_id=BROKEN").exists()
    assert rows == 2 * 48
    df = duckdb.sql(
        f"SELECT * FROM read_parquet('{out}/*/*.parquet', hive_partitioning = true)"
    ).df()
    assert df.groupby("assay_id")["position"].min().to_dict() == {"FULL": 0, "WINDOW": 3}


def test_failed_build_keeps_previous_output(tmp_path: Path, check: StructureCheck) -> None:
    out = tmp_path / "features"
    build_structure_features(check, out, PARAMS)
    before = sorted(p.name for p in out.iterdir())
    broken = write_variant(tmp_path, "bfactor")
    bad = StructureCheck(
        [
            type(m)(m.assay_id, broken, m.range_start, m.range_end, m.target_seq)
            for m in check.mappings
        ],
        check.allowed_exceptions,
    )
    with pytest.raises(StructureFeatureError, match=re.escape("bfactor.pdb")):
        build_structure_features(bad, out, PARAMS)
    assert sorted(p.name for p in out.iterdir()) == before
    assert not out.with_name("features.tmp").exists()


def test_structure_check_refuses_mapped_exceptions(check: StructureCheck) -> None:
    leaked = AllowedException("FULL", "reason", "failure")
    with pytest.raises(StructureMappingError, match="also mapped"):
        StructureCheck(check.mappings, [leaked])


def test_schema_rejects_gaps(check: StructureCheck, features: pd.DataFrame) -> None:
    full = next(m for m in check.mappings if m.assay_id == "FULL")
    table = assay_features(full, features).drop(index=10)
    with pytest.raises(pandera.errors.SchemaErrors, match="contiguous_positions"):
        StructureFeatureSchema.validate(table, lazy=True)
