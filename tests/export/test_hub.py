"""Staging and pushing the Hub dataset, on the toy dataset from tests/conftest.py. No network."""

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pytest
from omegaconf import OmegaConf
from typer.testing import CliRunner

from mutascope.cli import app
from mutascope.export import hub
from mutascope.export.hub import CHECKSUMS_FILE, PublishError, checksums, push_dataset
from mutascope.utils.config import load_config

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
    # conf/publish/hub.yaml now names the real repo, so clear it to exercise the guard.
    code, output = publish(processed_root, [*processed_overrides, "publish.repo_id=null"], "--push")
    assert code == 1
    assert "publish.repo_id" in output
    assert not (processed_root / "stage").exists()


@dataclass
class FakeCommit:
    commit_url: str
    oid: str = "abc"


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


def _bundle(root: Path) -> Path:
    """A two-row ``train final`` bundle for assay A (B is not eligible)."""
    import pandas as pd

    from mutascope.eval.schemas import FinalLandscapeSchema, FinalManifestSchema
    from mutascope.utils.io import write_partition

    bundle = root / "landscapes" / "ridge_seq_struct"
    bundle.mkdir(parents=True)
    version = {"code_version": "abc", "config_sha256": "0" * 64, "manifest_sha256": "1" * 64}
    landscape = pd.DataFrame(
        {
            "assay_id": "A",
            "position": [0, 0],
            "wt_aa": "M",
            "mt_aa": ["C", "A"],
            "position_status": "partially_measured",
            "measured": [False, True],
            "dms_score": [np.nan, 0.5],
            "prediction": [0.1, 0.4],
            "prediction_source": ["final_model", "out_of_fold"],
            "calibration_scheme": "random",
            "lower_80": [-0.5, -0.2],
            "upper_80": [0.7, 1.0],
            "lower_90": [-0.7, -0.4],
            "upper_90": [0.9, 1.2],
            "structure_imputed": False,
        }
        | version
    )
    with duckdb.connect() as con:
        write_partition(con, FinalLandscapeSchema.validate(landscape), bundle / "assay_id=A")
    manifest = pd.DataFrame(
        {
            "assay_id": ["A", "B"],
            "eligible": [True, False],
            "reason": [None, "no structure"],
            "default_model": ["ridge_seq_struct", "esm2_650m"],
            "has_structure": [True, False],
            "n_measured": [4, 2],
            "target_length": [4, 5],
            "landscape_rows": [2, 0],
        }
        | version
    )
    FinalManifestSchema.validate(manifest).to_parquet(bundle / "manifest.parquet")
    for name in ("fits.parquet", "coefficients.parquet"):
        (bundle / name).write_bytes(b"x")
    (bundle / "version.json").write_text("{}\n")
    return bundle


def test_stage_publishes_landscapes_as_new_configs(
    processed_root: Path, processed_overrides: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(processed_root)
    _bundle(processed_root)
    overrides = [*processed_overrides, "train.final.out_dir=landscapes"]
    code, output = publish(processed_root, overrides, "--landscapes")
    assert code == 0, output
    stage = processed_root / "stage"
    with duckdb.connect() as con:
        assert con.sql(
            f"SELECT assay_id, mt_aa FROM '{stage}/data/supervised_landscapes.parquet'"
        ).fetchall() == [("A", "A"), ("A", "C")]
        assert con.sql(
            f"SELECT count(*) FROM '{stage}/data/supervised_landscape_assays.parquet'"
        ).fetchone() == (2,)
    card = (stage / "README.md").read_text()
    assert "config_name: supervised_landscapes" in card
    assert checksums(stage) == (stage / CHECKSUMS_FILE).read_text()


def test_stage_model_copies_the_bundle_and_pushes_a_model_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    (tmp_path / "CARD.md").write_text(CARD_BODY)
    (tmp_path / "train.yaml").write_text("model: ridge\n")
    staged = hub.stage_model(
        bundle=bundle,
        files={"config/train.yaml": tmp_path / "train.yaml"},
        card=tmp_path / "CARD.md",
        card_metadata={"license": "mit"},
        out_dir=tmp_path / "stage",
    )
    files = sorted(p.relative_to(staged).as_posix() for p in staged.rglob("*") if p.is_file())
    assert files == [
        "README.md", "SHA256SUMS", "config/train.yaml", "model/coefficients.parquet",
        "model/fits.parquet", "model/manifest.parquet", "model/version.json",
    ]  # fmt: skip
    assert (staged / "README.md").read_text().startswith("---\nlicense: mit\n---\n\n# Toy card")
    with pytest.raises(PublishError, match="missing files"):
        hub.stage_model(
            bundle=bundle,
            files={"x": tmp_path / "absent"},
            card=tmp_path / "CARD.md",
            card_metadata={},
            out_dir=tmp_path / "stage2",
        )
    FakeHfApi.calls = []
    monkeypatch.setattr(hub, "HfApi", FakeHfApi)
    pushed = hub.push_folder(
        staged,
        "me/model",
        repo_type="model",
        private=True,
        message="m",
        delete_patterns=["model/*"],
    )
    assert pushed.oid == "abc"
    calls = dict(FakeHfApi.calls)
    assert calls["create_repo"]["repo_type"] == "model"
    assert calls["upload_folder"]["delete_patterns"] == ["model/*"]


def test_published_revisions_are_pinned_commits() -> None:
    """Later steps read the published repos by commit (ADR 0026 addendum), never by branch."""
    cfg = load_config(Path("conf"))
    pins = (
        cfg.publish.revision,
        cfg.train.publish.revision,
        cfg.export.publish.revision,
        cfg.onnx.publish.revision,
    )
    for revision in pins:
        assert re.fullmatch(r"[0-9a-f]{40}", str(revision)), revision
