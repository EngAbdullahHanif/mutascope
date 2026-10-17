"""benchmarks.json rows and the Hub side of the bundle: pinned reads and the tagged push."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from mutascope.export import hub
from mutascope.export.benchmarks import (
    BenchmarkError,
    caveats,
    comparison_rows,
    final_coverage,
    function_type_rows,
)
from mutascope.export.hub import CHECKSUMS_FILE, PublishError, checksums, fetch_pinned, push_bundle

REVISION = "a" * 40

# --- benchmarks.json -----------------------------------------------------------------------------


def _comparisons() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for group, split, diff, low, high in [
        ("Binding", "random", 0.29, 0.19, 0.38),
        ("Binding", "contiguous", 0.010, -0.106, 0.121),
        ("Binding", "modulo", 0.076, -0.033, 0.172),
        ("Stability", "contiguous", 0.16, 0.15, 0.18),
    ]:
        rows.append(
            {
                "model_a": "ridge_seq_struct",
                "model_b": "zero_shot_saprot_score",
                "split": split,
                "metric": "spearman",
                "level": "function",
                "group": group,
                "value_a": 0.5 + diff,
                "value_b": 0.5,
                "diff": diff,
                "ci_low": low,
                "ci_high": high,
                "share_a_better": 0.9,
                "n_assays": 13 if group == "Binding" else 66,
            }
        )
    other = dict(rows[0], model_a="lgbm_seq_struct", group="Activity")
    return pd.DataFrame([*rows, other])


def test_a_caveat_comes_from_paired_cis_that_include_zero() -> None:
    rows = function_type_rows(
        _comparisons(), deployed="ridge_seq_struct", comparator="zero_shot_saprot_score"
    )
    assert [(r.function_type, r.split, r.gain_shown) for r in rows] == [
        ("Binding", "contiguous", False),
        ("Binding", "modulo", False),
        ("Binding", "random", True),
        ("Stability", "contiguous", True),
    ]
    (caveat,) = caveats(rows, "SaProt 650M")
    assert caveat.function_type == "Binding" and caveat.n_assays == 13
    assert caveat.splits == ["contiguous", "modulo"]
    assert "(95% CI -0.106 to +0.121)" in caveat.text
    assert "SaProt 650M" in caveat.text


def test_function_type_rows_need_the_deployed_comparison() -> None:
    with pytest.raises(BenchmarkError):
        function_type_rows(_comparisons(), deployed="mlp", comparator="zero_shot_saprot_score")


def test_size_comparisons_become_size_level_rows() -> None:
    sizes = _comparisons().drop(columns=["level", "group"]).assign(size_bin="300-999")
    rows = comparison_rows(sizes)
    assert {(r.level, r.group, r.size_bin) for r in rows} == {("size", None, "300-999")}
    assert {r.size_bin for r in comparison_rows(_comparisons())} == {None}


def test_final_coverage_summarises_the_checks() -> None:
    checks = pd.DataFrame({"oof_coverage_80": [0.8, 0.82, 0.79], "oof_coverage_90": [0.9] * 3})
    c80, c90 = final_coverage(checks, [80, 90])
    assert (c80.level, c80.n_assays, c80.min, c80.max) == (0.8, 3, 0.79, 0.82)
    assert c80.mean == pytest.approx(0.80333333)
    assert c90.median == 0.9


# --- reading pinned revisions --------------------------------------------------------------------


@dataclass
class FakeRepo:
    """A Hub revision as files on disk, served the way ``hf_hub_download`` returns them."""

    root: Path
    calls: list[dict[str, str]] = field(default_factory=lambda: [])

    def __call__(self, *, repo_id: str, filename: str, repo_type: str, revision: str) -> str:
        self.calls.append({"filename": filename, "revision": revision, "repo_type": repo_type})
        return str(self.root / filename)


@pytest.fixture
def repo(tmp_path: Path) -> FakeRepo:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.parquet").write_bytes(b"table a")
    (tmp_path / "data" / "b.parquet").write_bytes(b"table b")
    (tmp_path / CHECKSUMS_FILE).write_text(checksums(tmp_path))
    return FakeRepo(tmp_path)


def test_fetch_reads_the_pinned_revision_and_checks_every_file(repo: FakeRepo) -> None:
    paths = fetch_pinned(
        "me/data",
        repo_type="dataset",
        revision=REVISION,
        filenames=["data/a.parquet"],
        download=repo,
    )
    assert paths["data/a.parquet"].read_bytes() == b"table a"
    assert {c["revision"] for c in repo.calls} == {REVISION}
    assert [c["filename"] for c in repo.calls] == [CHECKSUMS_FILE, "data/a.parquet"]


def test_fetch_refuses_a_branch_or_short_hash(repo: FakeRepo) -> None:
    for revision in ("main", "a" * 7):
        with pytest.raises(PublishError, match="not a full commit hash"):
            fetch_pinned(
                "me/data", repo_type="dataset", revision=revision, filenames=[], download=repo
            )


def test_fetch_refuses_a_file_that_differs_from_its_checksum(repo: FakeRepo) -> None:
    (repo.root / "data" / "b.parquet").write_bytes(b"changed")
    with pytest.raises(PublishError, match=r"b\.parquet sha256"):
        fetch_pinned(
            "me/data",
            repo_type="dataset",
            revision=REVISION,
            filenames=["data/b.parquet"],
            download=repo,
        )


def test_fetch_refuses_a_file_the_revision_does_not_list(repo: FakeRepo) -> None:
    (repo.root / "extra.json").write_text("{}")
    with pytest.raises(PublishError, match="not in SHA256SUMS"):
        fetch_pinned(
            "me/data",
            repo_type="dataset",
            revision=REVISION,
            filenames=["extra.json"],
            download=repo,
        )


# --- pushing the bundle --------------------------------------------------------------------------


@dataclass
class FakeCommit:
    commit_url: str
    oid: str


@dataclass
class FakeTag:
    name: str


@dataclass
class FakeRefs:
    tags: list[FakeTag]


class FakeHfApi:
    """Records calls instead of talking to the Hub; ``tags`` exist already."""

    calls: list[tuple[str, dict[str, Any]]] = []  # noqa: RUF012 — shared on purpose
    tags: list[str] = []  # noqa: RUF012

    def __init__(self, token: str | None = None) -> None:
        self.calls.append(("init", {"token": token}))

    def list_repo_refs(self, repo_id: str, **kwargs: Any) -> FakeRefs:
        return FakeRefs([FakeTag(t) for t in self.tags])

    def upload_folder(self, **kwargs: Any) -> FakeCommit:
        self.calls.append(("upload_folder", kwargs))
        return FakeCommit("https://huggingface.co/datasets/me/data/commit/b0b", "b" * 40)

    def create_tag(self, repo_id: str, **kwargs: Any) -> None:
        self.calls.append(("create_tag", {"repo_id": repo_id, **kwargs}))


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "bundle"
    (folder / "proteins").mkdir(parents=True)
    (folder / "manifest.json").write_text("{}")
    (folder / "proteins" / "A.json.gz").write_bytes(b"\x1f\x8b")
    (folder / CHECKSUMS_FILE).write_text(checksums(folder))
    FakeHfApi.calls, FakeHfApi.tags = [], []
    monkeypatch.setattr(hub, "HfApi", FakeHfApi)
    return folder


def test_push_replaces_only_the_bundle_folder_and_tags_the_commit(bundle: Path) -> None:
    pushed = push_bundle(bundle, "me/data", path_in_repo="bundle", tag="bundle-v1", message="m")
    assert pushed.oid == "b" * 40
    calls = dict(FakeHfApi.calls)
    upload = calls["upload_folder"]
    assert (upload["path_in_repo"], upload["repo_type"]) == ("bundle", "dataset")
    # Relative to path_in_repo (huggingface_hub), so the Phase 1 tables are left alone.
    assert upload["delete_patterns"] == ["*"]
    assert calls["create_tag"] == {
        "repo_id": "me/data",
        "tag": "bundle-v1",
        "revision": "b" * 40,
        "repo_type": "dataset",
        "tag_message": "m",
    }


def test_push_refuses_an_existing_tag(bundle: Path) -> None:
    FakeHfApi.tags = ["bundle-v1"]
    with pytest.raises(PublishError, match="already exists"):
        push_bundle(bundle, "me/data", path_in_repo="bundle", tag="bundle-v1", message="m")
    assert "upload_folder" not in dict(FakeHfApi.calls)


def test_push_refuses_a_bundle_changed_after_it_was_written(bundle: Path) -> None:
    (bundle / "manifest.json").write_text('{"edited": true}')
    with pytest.raises(PublishError, match="changed since it was written"):
        push_bundle(bundle, "me/data", path_in_repo="bundle", tag="bundle-v1", message="m")
    assert FakeHfApi.calls == []
    assert hashlib.sha256(b"{}").hexdigest() in (bundle / CHECKSUMS_FILE).read_text()
