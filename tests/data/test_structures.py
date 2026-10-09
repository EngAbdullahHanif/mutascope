"""Assay -> AF2 structure mapping must fail loudly on any mismatch."""

import csv
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mutascope.cli import app
from mutascope.data.structures import (
    StructureMappingError,
    map_structures,
    parse_pdb_range,
    pdb_sequence,
)

ONE_TO_THREE = {"M": "MET", "K": "LYS", "V": "VAL", "A": "ALA", "R": "ARG", "P": "PRO"}


def pdb_text(seq: str, chain: str = "A") -> str:
    """Minimal PDB: an N and a CA atom per residue (only CA must be read)."""
    lines: list[str] = []
    for i, aa in enumerate(seq, start=1):
        for atom in (" N  ", " CA "):
            lines.append(
                f"ATOM  {len(lines) + 1:>5} {atom} {ONE_TO_THREE[aa]} {chain}{i:>4}    "
                "   0.000   0.000   0.000  1.00 90.00           C"
            )
    return "\n".join([*lines, "END"])


def row(assay_id: str, target_seq: str, pdb_file: str, pdb_range: str) -> dict[str, str]:
    return {
        "DMS_id": assay_id,
        "target_seq": target_seq,
        "pdb_file": pdb_file,
        "pdb_range": pdb_range,
    }


def write_reference(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["DMS_id", "target_seq", "pdb_file", "pdb_range"])
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def structures(tmp_path: Path) -> Path:
    d = tmp_path / "structures"
    d.mkdir()
    (d / "FULL.pdb").write_text(pdb_text("MKVAR"))
    (d / "WINDOW.pdb").write_text(pdb_text("VA"))
    return d


def test_pdb_sequence_reads_ca_atoms() -> None:
    assert pdb_sequence(pdb_text("MKV")) == "MKV"


def test_pdb_sequence_rejects_multiple_chains() -> None:
    with pytest.raises(ValueError, match="one chain"):
        pdb_sequence(pdb_text("MK", chain="A") + "\n" + pdb_text("V", chain="B"))


@pytest.mark.parametrize("text", ["", "1", "1-", "a-5", "3_7"])
def test_parse_pdb_range_rejects_malformed(text: str) -> None:
    with pytest.raises(ValueError, match="malformed"):
        parse_pdb_range(text)


def test_maps_full_and_windowed_structures(tmp_path: Path, structures: Path) -> None:
    ref = write_reference(
        tmp_path / "ref.csv",
        [
            row("FULL_a", "MKVAR", "FULL.pdb", "1-5"),
            row("FULL_b", "MKVAR", "FULL.pdb", "1-5"),
            row("WIN", "MKVAR", "WINDOW.pdb", "3-4"),
        ],
    )
    mappings = map_structures(ref, structures)
    assert [(m.assay_id, m.pdb_path.name, m.range_start, m.range_end) for m in mappings] == [
        ("FULL_a", "FULL.pdb", 1, 5),
        ("FULL_b", "FULL.pdb", 1, 5),
        ("WIN", "WINDOW.pdb", 3, 4),
    ]


def test_reports_every_unmappable_assay(tmp_path: Path, structures: Path) -> None:
    ref = write_reference(
        tmp_path / "ref.csv",
        [
            row("OK", "MKVAR", "FULL.pdb", "1-5"),
            row("NOFILE", "MKVAR", "", "1-5"),
            row("MISSING", "MK", "GONE.pdb", "1-2"),
            row("PAST_END", "MKVA", "FULL.pdb", "1-5"),
            row("VARIANT", "MKPAR", "FULL.pdb", "1-5"),
            row("SHIFTED", "MKVAR", "WINDOW.pdb", "2-3"),
        ],
    )
    with pytest.raises(StructureMappingError) as excinfo:
        map_structures(ref, structures)

    message = str(excinfo.value)
    assert message.startswith("5 of 6 assays")
    assert "OK " not in message
    assert "NOFILE (row 3): no pdb_file" in message
    assert "MISSING (row 4): GONE.pdb not found" in message
    assert "PAST_END (row 5): pdb_range 1-5 outside target_seq of length 4" in message
    assert "VARIANT (row 6)" in message
    assert "P3V" in message  # target numbering: target has P at 3, structure has V
    assert "SHIFTED (row 7)" in message


def test_cli_check_structures_exit_codes(tmp_path: Path, structures: Path) -> None:
    good = write_reference(tmp_path / "good.csv", [row("A", "MKVAR", "FULL.pdb", "1-5")])
    bad = write_reference(tmp_path / "bad.csv", [row("A", "MKVAA", "FULL.pdb", "1-5")])
    runner = CliRunner()
    args = ["data", "check-structures", "--structures-dir", str(structures), "--reference"]

    ok = runner.invoke(app, [*args, str(good)])
    assert ok.exit_code == 0, ok.output
    assert "1 assays mapped" in ok.stdout

    failed = runner.invoke(app, [*args, str(bad)])
    assert failed.exit_code == 1
    assert "A5R" in failed.stderr  # target A, structure R
