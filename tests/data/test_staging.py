"""Staging inputs: the full release linked, or named assays copied, in one fixed layout."""

import csv
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mutascope.cli import app
from mutascope.data.release import Release, RemoteFile
from mutascope.data.staging import SubsetError, link_release, subset_release

FIELDS = ["DMS_id", "target_seq", "pdb_file", "pdb_range", "coarse_selection_type"]
ROWS = [
    ["A", "MKV", "P1.pdb", "1-3", "Activity"],
    ["B", "MKVA", "P2.pdb", "1-4", "Binding"],
    ["C", "MK", "P1.pdb", "1-2", "Stability"],  # shares P1.pdb with A
    ["D", "MKVAR", "", "", "Expression"],  # no structure upstream
]


@pytest.fixture
def release(tmp_path: Path) -> tuple[Path, Path, Path]:
    raw = tmp_path / "raw"
    dms = raw / "dms"
    structures = raw / "structures"
    dms.mkdir(parents=True)
    structures.mkdir()
    reference = raw / "DMS_substitutions.csv"
    with reference.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        writer.writerows(ROWS)
    for assay_id, *_ in ROWS:
        (dms / f"{assay_id}.csv").write_text(f"mutant\n{assay_id}\n")
    for name in ("P1.pdb", "P2.pdb"):
        (structures / name).write_text(f"ATOM {name}\n")
    return reference, dms, structures


def read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="") as fh:
        return list(csv.reader(fh))


def test_keeps_named_assays_in_reference_order(
    release: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    out = subset_release(*release, ["C", "A"], tmp_path / "out")
    assert read_rows(out.reference) == [FIELDS, ROWS[0], ROWS[2]]  # every column, original order
    assert sorted(p.name for p in out.dms_dir.iterdir()) == ["A.csv", "C.csv"]
    assert (out.dms_dir / "C.csv").read_text() == "mutant\nC\n"
    assert [p.name for p in out.structures_dir.iterdir()] == ["P1.pdb"]  # shared PDB, once
    assert out.pdb_files == 1


def test_assay_without_structure_is_left_to_the_structure_check(
    release: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    (release[2] / "P2.pdb").unlink()
    out = subset_release(*release, ["B", "D"], tmp_path / "out")
    assert out.pdb_files == 0
    assert list(out.structures_dir.iterdir()) == []
    assert [r[0] for r in read_rows(out.reference)[1:]] == ["B", "D"]


@pytest.mark.parametrize(
    ("assays", "message"),
    [
        ([], "no assays requested"),
        (["A", "A"], "more than once: A"),
        (["A", "GHOST", "ZZZ"], "not in DMS_substitutions.csv: GHOST, ZZZ"),
    ],
)
def test_bad_requests_fail(
    release: tuple[Path, Path, Path], tmp_path: Path, assays: list[str], message: str
) -> None:
    with pytest.raises(SubsetError, match=message):
        subset_release(*release, assays, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_missing_dms_csv_fails(release: tuple[Path, Path, Path], tmp_path: Path) -> None:
    (release[1] / "B.csv").unlink()
    with pytest.raises(SubsetError, match=r"no DMS CSV in .*: B"):
        subset_release(*release, ["A", "B"], tmp_path / "out")


def test_never_merges_into_a_stale_subset(release: tuple[Path, Path, Path], tmp_path: Path) -> None:
    subset_release(*release, ["A"], tmp_path / "out")
    with pytest.raises(SubsetError, match="is not empty"):
        subset_release(*release, ["B"], tmp_path / "out")


def test_cli_subset(release: tuple[Path, Path, Path], tmp_path: Path) -> None:
    reference, dms, structures = release
    args = ["data", "subset", "--reference", str(reference), "--dms-dir", str(dms)]
    args += ["--structures-dir", str(structures), "--out-dir", str(tmp_path / "out")]

    ok = CliRunner().invoke(app, [*args, "--assay", "A", "--assay", "B"])
    assert ok.exit_code == 0, ok.output
    assert "2 assays, 2 PDB files" in ok.stdout

    failed = CliRunner().invoke(app, [*args[:-1], str(tmp_path / "other"), "--assay", "X"])
    assert failed.exit_code == 1
    assert "not in DMS_substitutions.csv: X" in failed.stderr


def release_of(raw_dir: Path) -> Release:
    def rf(key: str, filename: str, archive: bool) -> RemoteFile:
        return RemoteFile(key, filename, f"https://example.org/{filename}", 1, None, archive)

    files = (
        rf("reference", "DMS_substitutions.csv", False),
        rf("dms_substitutions", "DMS_ProteinGym_substitutions.zip", True),
        rf("af2_structures", "ProteinGym_AF2_structures.zip", True),
        rf("cv_folds_singles", "cv_folds.zip", True),
    )
    return Release("v9", raw_dir, raw_dir / "manifest.yaml", files)


def test_link_release_points_layout_at_raw_download(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "DMS_ProteinGym_substitutions").mkdir(parents=True)
    (raw / "ProteinGym_AF2_structures").mkdir()
    (raw / "cv_folds").mkdir()
    (raw / "DMS_substitutions.csv").write_text("DMS_id\n")
    (raw / "DMS_ProteinGym_substitutions" / "A.csv").write_text("mutant\n")

    link_release(release_of(raw), tmp_path / "task")
    task = tmp_path / "task"
    assert sorted(p.name for p in task.iterdir()) == [
        "DMS_substitutions.csv",
        "dms",
        "folds",
        "structures",
    ]
    assert (task / "folds").resolve() == (raw / "cv_folds").resolve()
    assert all((task / name).is_symlink() for name in ("DMS_substitutions.csv", "dms"))
    assert (task / "dms").resolve() == (raw / "DMS_ProteinGym_substitutions").resolve()
    assert (task / "dms" / "A.csv").read_text() == "mutant\n"
    assert (task / "structures").readlink().is_absolute()


def test_link_release_refuses_incomplete_download(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "DMS_substitutions.csv").write_text("DMS_id\n")
    with pytest.raises(FileNotFoundError, match="DMS_ProteinGym_substitutions"):
        link_release(release_of(raw), tmp_path / "task")
    assert not (tmp_path / "task").exists()
