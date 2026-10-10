"""Shared fixtures for the structure tests: one real ProteinGym AF2 structure, mapped two ways."""

import csv
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from mutascope.data.structures import StructureCheck, map_structures, pdb_sequence

FIXTURES = Path(__file__).parent / "fixtures"
PDB_NAME = "SR43C_ARATH.pdb"
"""ProteinGym v1.3 AF2 structure of SR43C_ARATH_Tsuboyama_2023_2N88 (48 residues, explicit H)."""

EXCEPTION_ID = "BROKEN"


def write_reference(path: Path, rows: list[dict[str, str]]) -> Path:
    """A minimal ProteinGym reference CSV with the columns the structure steps read."""
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["DMS_id", "target_seq", "pdb_file", "pdb_range"])
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def structures_dir(tmp_path: Path) -> Path:
    d = tmp_path / "structures"
    d.mkdir()
    (d / PDB_NAME).write_text((FIXTURES / PDB_NAME).read_text())
    return d


@pytest.fixture
def fixture_seq() -> str:
    return pdb_sequence((FIXTURES / PDB_NAME).read_text())


@pytest.fixture
def reference(tmp_path: Path, fixture_seq: str) -> Path:
    """Three assays on the fixture PDB: whole target, windowed target, and a documented exception.

    ``WINDOW`` has 3 extra residues before and 2 after the structure, so its structure starts
    at 0-based position 3. ``BROKEN`` has one residue changed, like the P53 R72P exceptions.
    """
    n = len(fixture_seq)
    broken = fixture_seq[:5] + ("A" if fixture_seq[5] != "A" else "G") + fixture_seq[6:]
    return write_reference(
        tmp_path / "reference.csv",
        [
            {"DMS_id": "FULL", "target_seq": fixture_seq, "pdb_file": PDB_NAME,
             "pdb_range": f"1-{n}"},
            {"DMS_id": "WINDOW", "target_seq": "MKV" + fixture_seq + "GS", "pdb_file": PDB_NAME,
             "pdb_range": f"4-{n + 3}"},
            {"DMS_id": EXCEPTION_ID, "target_seq": broken, "pdb_file": PDB_NAME,
             "pdb_range": f"1-{n}"},
        ],
    )  # fmt: skip


@pytest.fixture
def check(reference: Path, structures_dir: Path) -> StructureCheck:
    return map_structures(reference, structures_dir, {EXCEPTION_ID: "documented natural variant"})


PINNED_FOLDSEEK = "941cd33ff0771cd2e3f144e3293e22a2b87e9fda"

FakeFoldseek = Callable[[str, str], Path]


@pytest.fixture
def fake_foldseek(tmp_path: Path) -> FakeFoldseek:
    """Make a script standing in for Foldseek: ``(version, output rows) -> path``.

    It answers ``version`` and writes the rows (printf escapes allowed) to the descriptor
    output path, which is the argument just before ``--chain-name-mode`` in ``run_foldseek``.
    """

    def make(version: str, rows: str) -> Path:
        script = tmp_path / "foldseek"
        script.write_text(
            "#!/bin/sh\n"
            f'if [ "$1" = version ]; then echo {version}; exit 0; fi\n'
            'prev=""\n'
            'for a in "$@"; do\n'
            '  if [ "$a" = "--chain-name-mode" ]; then out="$prev"; fi\n'
            '  prev="$a"\n'
            "done\n"
            f"printf '{rows}' > \"$out\"\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return script

    return make
