"""Startup refuses a bundle it cannot vouch for: checksums, schema version, missing files."""

import gzip
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from api.app.bundle import (
    Bundle,
    BundleError,
    Source,
    fetch_bundle,
    open_bundle,
)
from api.app.main import create_app
from api.app.settings import Settings
from api.tests.conftest import SUPERVISED

pytestmark = pytest.mark.anyio


def _copy(bundle_dir: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(bundle_dir, tmp_path / "bundle"))


def _rewrite(root: Path, name: str, data: dict[str, Any]) -> None:
    """Replace a top-level file and its checksum line (a self-consistent but other bundle)."""
    text = json.dumps(data)
    (root / name).write_text(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    sums = root / "SHA256SUMS"
    lines = [
        f"{digest}  {name}" if line.endswith(f"  {name}") else line
        for line in sums.read_text().splitlines()
    ]
    sums.write_text("\n".join(lines) + "\n")


def _open(root: Path) -> Bundle:
    return Bundle(Source(None, None, root), cache_size=4, require_all=False)


async def test_startup_refuses_an_unsupported_schema_version(
    bundle_dir: Path, onnx_dir: Path, tmp_path: Path
) -> None:
    root = _copy(bundle_dir, tmp_path)
    manifest = json.loads((root / "manifest.json").read_text())
    _rewrite(root, "manifest.json", manifest | {"schema_version": "2.0.0"})
    app = create_app(
        Settings(bundle_dir=root, onnx_local_dir=onnx_dir, lock_file=root / "api.lock")
    )
    with pytest.raises(BundleError, match=r"schema version '2\.0\.0'"):
        async with app.router.lifespan_context(app):
            pass
    assert app.state.mutascope is None


def test_checksum_mismatch_is_refused(bundle_dir: Path, tmp_path: Path) -> None:
    root = _copy(bundle_dir, tmp_path)
    with (root / "benchmarks.json").open("a") as f:
        f.write(" ")
    with pytest.raises(BundleError, match=r"benchmarks\.json: sha256 differs"):
        _open(root)


def test_a_snapshot_must_hold_every_listed_file(bundle_dir: Path, tmp_path: Path) -> None:
    """A local copy may hold some protein files; a Hub snapshot must hold them all."""
    root = _copy(bundle_dir, tmp_path)
    assert len(_open(root).entries) == 217
    with pytest.raises(BundleError, match="listed in SHA256SUMS but missing"):
        Bundle(Source("repo", "0" * 40, root), cache_size=4, require_all=True)
    (root / "manifest.json").unlink()
    with pytest.raises(BundleError, match=r"manifest\.json is listed"):
        _open(root)


def test_protein_files_are_checked_at_startup_and_when_read(
    bundle_dir: Path, tmp_path: Path
) -> None:
    root = _copy(bundle_dir, tmp_path)
    bundle = _open(root)
    path = root / "proteins" / f"{SUPERVISED}.json.gz"
    data = json.loads(gzip.decompress(path.read_bytes()))
    data["assay"]["molecule_name"] = "edited"
    path.write_bytes(gzip.compress(json.dumps(data).encode()))
    with pytest.raises(BundleError, match="sha256 differs from the manifest"):
        bundle.protein(SUPERVISED)  # changed after startup
    with pytest.raises(BundleError, match="sha256 differs from SHA256SUMS"):
        _open(root)


def test_fetch_reads_the_bundle_folder_at_the_pinned_commit(
    bundle_dir: Path, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def download(**kwargs: Any) -> str:
        calls.append(kwargs)
        shutil.copytree(bundle_dir, tmp_path / "snapshot" / "bundle")
        return str(tmp_path / "snapshot")

    revision = "3ae767217bb0a56b8f2e7c79c68b73119e308066"
    source = fetch_bundle("org/repo", "bundle", revision, download)
    assert source == Source("org/repo", revision, tmp_path / "snapshot" / "bundle")
    assert calls == [
        {
            "repo_id": "org/repo",
            "repo_type": "dataset",
            "revision": revision,
            "allow_patterns": ["bundle/*", "bundle/**/*"],
        }
    ]

    def same_snapshot(**_: Any) -> str:
        return str(tmp_path / "snapshot")

    # The fixture lacks most protein files, so a snapshot of it is refused.
    with pytest.raises(BundleError, match="missing"):
        open_bundle(
            repo="org/repo",
            path_in_repo="bundle",
            revision=revision,
            local_dir=None,
            cache_size=4,
            download=same_snapshot,
        )


@pytest.mark.parametrize("revision", ["main", "bundle-v1.0.0", "3ae7672"])
def test_fetch_refuses_anything_but_a_commit_hash(revision: str) -> None:
    def download(**_: Any) -> str:
        raise AssertionError("must not download")

    with pytest.raises(BundleError, match="full commit hash"):
        fetch_bundle("org/repo", "bundle", revision, download)
