"""Foldseek 3Di: output parsing, version pinning and sequence checks, with a fake binary.

One test runs the real pinned Foldseek on the fixture and is skipped where it isn't installed
(CI); every other test uses a shell script standing in for it.
"""

import os
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

from mutascope.data.structures import StructureCheck
from mutascope.data.three_di import (
    Descriptor,
    FoldseekConfig,
    FoldseekError,
    assay_three_di,
    build_three_di,
    check_foldseek,
    parse_descriptors,
)

PINNED = "941cd33ff0771cd2e3f144e3293e22a2b87e9fda"
FIXTURE_3DI = "DAFPAFPDWDQDPVNPWIWTFTDHPVDNHTDTDTPVPDDVVRVVVVVD"
"""3Di of the fixture from Foldseek 10-941cd33 (same as the full ProteinGym run)."""


def test_parse_strips_pdb_title_after_name() -> None:
    """Regression: 13 ProteinGym PDBs have a TITLE, which Foldseek appends to the name."""
    text = (
        "AICDA_HUMAN.pdb_A ALPHAFOLD MONOMER V2.0 PREDICTION FOR DEAMINASE (Q9GZX7)\tMKV\tDPV\t0\n"
        "BLAT_ECOLX.pdb_A\tMS\tDA\t0\n"
    )
    assert parse_descriptors(text) == {
        "AICDA_HUMAN.pdb": Descriptor("MKV", "DPV"),
        "BLAT_ECOLX.pdb": Descriptor("MS", "DA"),
    }


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("X.pdb_B\tMK\tDP\t0\n", "not chain A"),
        ("X.pdb_A\tMK\tDP\t0\nX.pdb_A\tMK\tDP\t0\n", "appears twice"),
        ("X.pdb_A\tMKV\tDP\t0\n", "3 residues but 2 3Di states"),
        ("X.pdb_A\tMK\n", "expected >= 3"),
    ],
)
def test_parse_rejects_unexpected_output(text: str, message: str) -> None:
    with pytest.raises(FoldseekError, match=message):
        parse_descriptors(text)


def config(script: Path) -> FoldseekConfig:
    return FoldseekConfig(bin=str(script), version=PINNED, threads=1)


def test_wrong_or_missing_binary_is_refused(
    tmp_path: Path, fake_foldseek: Callable[[str, str], Path]
) -> None:
    wrong = config(fake_foldseek("0000000", ""))
    with pytest.raises(FoldseekError, match="config pins"):
        check_foldseek(wrong)
    with pytest.raises(FoldseekError, match="not found"):
        check_foldseek(FoldseekConfig(str(tmp_path / "nope"), PINNED, 1))


def test_foldseek_sequence_must_match_target(check: StructureCheck) -> None:
    full = next(m for m in check.mappings if m.assay_id == "FULL")
    wrong = Descriptor("A" * len(full.target_seq), "D" * len(full.target_seq))
    with pytest.raises(FoldseekError, match="differs from target_seq"):
        assay_three_di(full, wrong)


def test_build_with_fake_foldseek(
    tmp_path: Path,
    check: StructureCheck,
    fixture_seq: str,
    fake_foldseek: Callable[[str, str], Path],
) -> None:
    script = fake_foldseek(PINNED, f"SR43C_ARATH.pdb_A\\t{fixture_seq}\\t{FIXTURE_3DI}\\t0\\n")
    out = tmp_path / "three_di"
    rows = build_three_di(check, out, config(script))

    assert rows == 2 * len(fixture_seq)
    assert sorted(p.name for p in out.iterdir()) == ["assay_id=FULL", "assay_id=WINDOW"]
    strings = duckdb.sql(
        f"SELECT assay_id, min(position) AS first, string_agg(three_di, '' ORDER BY position) AS s "
        f"FROM read_parquet('{out}/*/*.parquet', hive_partitioning = true) GROUP BY ALL"
    ).df()
    assert dict(zip(strings["assay_id"], strings["s"], strict=True)) == {
        "FULL": FIXTURE_3DI,
        "WINDOW": FIXTURE_3DI,
    }
    assert dict(zip(strings["assay_id"], strings["first"], strict=True)) == {"FULL": 0, "WINDOW": 3}


def test_missing_descriptor_fails(
    tmp_path: Path, check: StructureCheck, fake_foldseek: Callable[[str, str], Path]
) -> None:
    script = fake_foldseek(PINNED, "OTHER.pdb_A\\tMK\\tDP\\t0\\n")
    with pytest.raises(FoldseekError, match="no chain A descriptor"):
        build_three_di(check, tmp_path / "out", config(script))


def test_real_foldseek_on_fixture(tmp_path: Path, check: StructureCheck) -> None:
    real = FoldseekConfig(os.environ.get("FOLDSEEK_BIN", "foldseek"), PINNED, threads=1)
    try:
        check_foldseek(real)
    except FoldseekError as exc:
        pytest.skip(f"pinned Foldseek not available: {exc}")
    out = tmp_path / "three_di"
    build_three_di(check, out, real)
    [(s,)] = duckdb.sql(
        f"SELECT string_agg(three_di, '' ORDER BY position) "
        f"FROM read_parquet('{out}/assay_id=FULL/*.parquet')"
    ).fetchall()
    assert s == FIXTURE_3DI
