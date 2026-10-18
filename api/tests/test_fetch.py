"""The image's build-time fetch: the served ONNX files are checked against the pinned SHA256SUMS."""

import hashlib
from pathlib import Path

import pytest

from api.app.fetch import FetchError, fetch_onnx, parse_checksums, served_onnx_files, sha256_file
from api.app.settings import Settings


class FakeHub:
    """``hf_hub_download`` over a local folder, recording what was asked for."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, repo_id: str, filename: str, revision: str) -> Path:
        self.calls.append((repo_id, filename, revision))
        return self.root / filename


def _write(root: Path, name: str, content: bytes) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def hub(tmp_path: Path) -> FakeHub:
    """Every served file with a listed digest, plus a file the image does not need."""
    lines: list[str] = []
    for model in served_onnx_files(Settings()):
        for name in model.files:
            lines.append(f"{_write(tmp_path, name, name.encode())}  {name}")
    lines.append(
        f"{_write(tmp_path, 'esm2_35m/int8/model.onnx', b'int8')}  esm2_35m/int8/model.onnx"
    )
    (tmp_path / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    return FakeHub(tmp_path)


def test_served_files_are_the_fp32_graphs_and_tokenizers() -> None:
    files = {name for model in served_onnx_files(Settings()) for name in model.files}
    assert files == {
        "esm2_35m/fp32/model.onnx",
        "esm2_35m/tokenizer.json",
        "esm2_150m/fp32/model.onnx",
        "esm2_150m/tokenizer.json",
    }


def test_fetch_checks_every_file_at_the_pinned_revision(hub: FakeHub) -> None:
    settings = Settings()
    assert fetch_onnx(settings, download=hub) == 4
    assert {revision for _, _, revision in hub.calls} == {settings.onnx_revision}
    fetched = {name for _, name, _ in hub.calls}
    assert "esm2_35m/int8/model.onnx" not in fetched  # int8 is never baked in (ADR 0029)
    assert [name for _, name, _ in hub.calls].count("SHA256SUMS") == 1


def test_a_changed_file_fails(hub: FakeHub) -> None:
    (hub.root / "esm2_150m/fp32/model.onnx").write_bytes(b"tampered")
    with pytest.raises(FetchError, match=r"esm2_150m/fp32/model\.onnx: sha256 differs"):
        fetch_onnx(Settings(), download=hub)


def test_an_unlisted_file_fails(hub: FakeHub) -> None:
    sums = hub.root / "SHA256SUMS"
    kept = [line for line in sums.read_text().splitlines() if "esm2_35m/tokenizer" not in line]
    sums.write_text("\n".join(kept) + "\n")
    with pytest.raises(FetchError, match=r"esm2_35m/tokenizer\.json is not listed"):
        fetch_onnx(Settings(), download=hub)


def test_checksum_helpers(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x" * (3 * 2**20 + 5))  # more than one chunk
    assert sha256_file(path) == hashlib.sha256(path.read_bytes()).hexdigest()
    assert parse_checksums("aa  a b.txt\n\nbb  c\n") == {"a b.txt": "aa", "c": "bb"}
