"""Staging and pushing the Hub dataset, on the toy dataset from tests/conftest.py. No network."""

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pytest
from omegaconf import OmegaConf
from typer.testing import CliRunner

from mutascope.cli import app
from mutascope.export import hub
from mutascope.export.hub import CHECKSUMS_FILE, PublishError, checksums, push_dataset

CONF = Path(__file__).parents[2] / "conf"
CARD_BODY = "# Toy card\n\nBody.\n"


def publish(root: Path, overrides: list[str], *extra: str) -> tuple[int, str]:
    """Run ``mutascope data publish`` from ``root`` (config paths are relative to the cwd)."""
    (root / "CARD.md").write_text(CARD_BODY)
    args = ["data", "publish", "--config-dir", str(CONF), *extra, *overrides]
    args += ["publish.card=CARD.md", "publish.stage_dir=stage"]
    result = CliRunner().invoke(app, args)
    return result.exit_code, result.output


@pytest.fixture
def staged(
    processed_root: Path, processed_overrides: list[str], monkeypatch: pytest.MonkeyPatch
) -> Path:
    monkeypatch.chdir(processed_root)
    code, output = publish(processed_root, processed_overrides)
    assert code == 0, output
    assert "not pushed" in output
    return processed_root / "stage"


def test_stage_writes_tables_reports_card_and_checksums(staged: Path) -> None:
    with duckdb.connect() as con:
        rows = {
            name: con.sql(f"SELECT count(*) FROM '{staged / 'data' / name}.parquet'").fetchone()
            for name in ("variants", "assays", "structure_features", "three_di")
        }
        assert rows == {
            "variants": (6,),
            "assays": (2,),
            "structure_features": (5,),
            "three_di": (5,),
        }
        # assay_id is a real column (the Hub does not read hive paths), rows are sorted.
        assert con.sql(
            f"SELECT assay_id, mutant FROM '{staged}/data/variants.parquet'"
        ).fetchall() == [("A", "M1A"), ("A", "K2C"), ("A", "V3G"), ("A", "A4C"),
                         ("B", "M1A"), ("B", "R5W")]  # fmt: skip
        assert con.sql(
            f"SELECT assay_id, has_structure FROM '{staged}/data/assays.parquet'"
        ).fetchall() == [("A", True), ("B", False)]

    assert sorted(p.name for p in (staged / "reports").iterdir()) == sorted(hub.REPORT_FILES)
    provenance = json.loads((staged / "provenance.json").read_text())
    assert provenance["proteingym_release"]["version"] == "v1.3"
    assert provenance["rows"]["variants"] == 6

    card = (staged / "README.md").read_text()
    _, front, body = card.split("---\n", 2)
    meta = OmegaConf.create(front)
    assert meta.license == "mit"
    assert [str(c.config_name) for c in meta.configs] == [t.name for t in hub.TABLES]
    assert meta.configs[0].default is True
    assert body == "\n" + CARD_BODY

    assert (staged / CHECKSUMS_FILE).read_text() == checksums(staged)


def test_staging_is_byte_identical_when_repeated(
    staged: Path, processed_root: Path, processed_overrides: list[str]
) -> None:
    first = (staged / CHECKSUMS_FILE).read_text()
    code, output = publish(processed_root, processed_overrides)
    assert code == 0, output
    assert (staged / CHECKSUMS_FILE).read_text() == first


def test_count_mismatch_with_reference_fails_and_stages_nothing(
    processed_root: Path, processed_overrides: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = processed_root / "DMS_substitutions.csv"
    with reference.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    rows[1]["DMS_number_single_mutants"] = "3"  # B really has 2
    with reference.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    monkeypatch.chdir(processed_root)
    code, output = publish(processed_root, processed_overrides)
    assert code == 1
    assert "B (reference 3, processed 2)" in output
    assert not (processed_root / "stage").exists()


def test_missing_report_fails(
    processed_root: Path, processed_overrides: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (processed_root / "results" / "structure_report.md").unlink()
    monkeypatch.chdir(processed_root)
    code, output = publish(processed_root, processed_overrides)
    assert code == 1
    assert "structure_report.md" in output


def test_push_needs_a_repo_id(
    processed_root: Path, processed_overrides: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(processed_root)
    code, output = publish(processed_root, processed_overrides, "--push")
    assert code == 1
    assert "publish.repo_id" in output
    assert not (processed_root / "stage").exists()


@dataclass
class FakeCommit:
    commit_url: str


class FakeHfApi:
    """Records calls instead of talking to the Hub."""

    calls: list[tuple[str, dict[str, Any]]] = []  # noqa: RUF012 — shared on purpose

    def __init__(self, token: str | None = None) -> None:
        self.calls.append(("init", {"token": token}))

    def create_repo(self, repo_id: str, **kwargs: Any) -> None:
        self.calls.append(("create_repo", {"repo_id": repo_id, **kwargs}))

    def upload_folder(self, **kwargs: Any) -> FakeCommit:
        self.calls.append(("upload_folder", kwargs))
        return FakeCommit("https://huggingface.co/datasets/me/toy/commit/abc")


def test_push_creates_a_private_dataset_repo_and_uploads_the_folder(
    staged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeHfApi.calls = []
    monkeypatch.setattr(hub, "HfApi", FakeHfApi)
    url = push_dataset(staged, "me/toy", private=True)
    assert url.endswith("/commit/abc")
    calls = dict(FakeHfApi.calls)
    assert calls["create_repo"] == {
        "repo_id": "me/toy",
        "repo_type": "dataset",
        "private": True,
        "exist_ok": True,
    }
    assert calls["upload_folder"]["folder_path"] == staged
    assert calls["upload_folder"]["repo_type"] == "dataset"


def test_push_refuses_a_folder_changed_after_staging(
    staged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeHfApi.calls = []
    monkeypatch.setattr(hub, "HfApi", FakeHfApi)
    (staged / "README.md").write_text("edited by hand\n")
    with pytest.raises(PublishError, match="changed since it was staged"):
        push_dataset(staged, "me/toy", private=True)
    assert FakeHfApi.calls == []
