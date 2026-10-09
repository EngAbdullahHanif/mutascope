"""Downloader: checksum verification, idempotency, extraction and Parquet conversion (offline)."""

import hashlib
import io
import zipfile
from collections.abc import Callable
from pathlib import Path

import duckdb
import httpx
import pytest

from mutascope.data.download import (
    ArchiveContentError,
    DownloadError,
    ensure_file,
    fetch_release,
)
from mutascope.data.manifest import Manifest, ManifestEntry
from mutascope.data.release import ParquetConversion, Release, RemoteFile

BASE = "https://example.org/pg"


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def remote(
    filename: str,
    payload: bytes,
    *,
    archive: bool = False,
    to_parquet: ParquetConversion | None = None,
    publish_md5: bool = True,
) -> RemoteFile:
    return RemoteFile(
        key=filename,
        filename=filename,
        url=f"{BASE}/{filename}",
        size_bytes=len(payload),
        md5=hashlib.md5(payload).hexdigest() if publish_md5 else None,
        archive=archive,
        to_parquet=to_parquet,
    )


class Server:
    """Mock HTTP server: serves fixed payloads and counts requests."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        name = request.url.path.rsplit("/", 1)[-1]
        if name not in self.files:
            return httpx.Response(404)
        return httpx.Response(200, content=self.files[name])

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def run(tmp_path: Path) -> Callable[[RemoteFile, Server, ManifestEntry | None], ManifestEntry]:
    def _run(rf: RemoteFile, server: Server, recorded: ManifestEntry | None) -> ManifestEntry:
        with server.client() as client:
            return ensure_file(rf, tmp_path, client, recorded)

    return _run


CSV = b"DMS_id,target_seq\nA_1,MKV\n"
RunFn = Callable[[RemoteFile, Server, ManifestEntry | None], ManifestEntry]


def test_download_verifies_md5_and_records_sha256(tmp_path: Path, run: RunFn) -> None:
    entry = run(remote("ref.csv", CSV), Server({"ref.csv": CSV}), None)

    assert (tmp_path / "ref.csv").read_bytes() == CSV
    assert entry.sha256 == hashlib.sha256(CSV).hexdigest()
    assert entry.md5 == hashlib.md5(CSV).hexdigest()
    assert entry.size_bytes == len(CSV)


def test_existing_verified_file_is_not_downloaded_again(run: RunFn) -> None:
    rf, server = remote("ref.csv", CSV), Server({"ref.csv": CSV})
    entry = run(rf, server, None)
    assert run(rf, server, entry) == entry
    assert len(server.requests) == 1


def test_md5_mismatch_fails_and_leaves_no_file(tmp_path: Path, run: RunFn) -> None:
    rf = remote("ref.csv", CSV)
    tampered = CSV.replace(b"MKV", b"MKW")  # same size, different content
    with pytest.raises(DownloadError, match="md5"):
        run(rf, Server({"ref.csv": tampered}), None)
    assert list(tmp_path.iterdir()) == []


def test_size_mismatch_fails(run: RunFn) -> None:
    with pytest.raises(DownloadError, match="size"):
        run(remote("ref.csv", CSV), Server({"ref.csv": CSV + b"B_2,AA\n"}), None)


def test_local_file_differing_from_manifest_fails_loudly(tmp_path: Path, run: RunFn) -> None:
    rf = remote("ref.csv", CSV, publish_md5=False)
    server = Server({"ref.csv": CSV})
    entry = run(rf, server, None)
    (tmp_path / "ref.csv").write_bytes(CSV.replace(b"MKV", b"MKW"))

    with pytest.raises(DownloadError, match="sha256"):
        run(rf, server, entry)
    assert len(server.requests) == 1  # never "repaired" by re-downloading


def test_http_error_propagates(run: RunFn) -> None:
    with pytest.raises(httpx.HTTPStatusError):
        run(remote("ref.csv", CSV), Server({}), None)


def test_archive_is_extracted_flat_without_macos_junk(tmp_path: Path, run: RunFn) -> None:
    payload = make_zip(
        {
            "structures/A.pdb": b"pdb-a",
            "structures/B.pdb": b"pdb-b",
            "structures/.DS_Store": b"junk",
            "__MACOSX/structures/._A.pdb": b"junk",
        }
    )
    rf = remote("structures.zip", payload, archive=True)
    run(rf, Server({"structures.zip": payload}), None)

    out = tmp_path / "structures"
    assert sorted(p.name for p in out.iterdir()) == ["A.pdb", "B.pdb"]
    assert (out / "A.pdb").read_bytes() == b"pdb-a"
    assert (tmp_path / "structures.zip").exists()  # small archives are kept


def test_archive_with_duplicate_basenames_is_rejected(tmp_path: Path, run: RunFn) -> None:
    payload = make_zip({"a/X.pdb": b"1", "b/X.pdb": b"2"})
    with pytest.raises(ArchiveContentError, match="duplicate"):
        run(remote("s.zip", payload, archive=True), Server({"s.zip": payload}), None)
    assert not (tmp_path / "s").exists()


SCORES = {
    "AS1_HUMAN.csv": b"mutant,mutated_sequence,DMS_score,DMS_score_bin,ESM2_8M,Other\n"
    b"M1A,AKV,0.5,1,-1.25,9\nK2A:V3A,MAA,-0.5,0,,9\n",
    "AS2_YEAST.csv": b"mutant,mutated_sequence,DMS_score,DMS_score_bin,ESM2_8M,Other\n"
    b"G1C,CA,1.5,1,0.25,9\n",
}
CONVERSION = ParquetConversion(
    columns=(
        ("mutant", "VARCHAR"),
        ("DMS_score", "DOUBLE"),
        ("DMS_score_bin", "INTEGER"),
        ("ESM2_8M", "DOUBLE"),
    ),
    delete_archive=True,
)


def test_archive_converted_to_partitioned_parquet_and_deleted(tmp_path: Path, run: RunFn) -> None:
    payload = make_zip(SCORES)
    rf = remote("scores.zip", payload, archive=True, to_parquet=CONVERSION)
    server = Server({"scores.zip": payload})
    entry = run(rf, server, None)

    assert not (tmp_path / "scores.zip").exists()
    rows = duckdb.sql(
        f"SELECT assay_id, mutant, DMS_score, DMS_score_bin, ESM2_8M "
        f"FROM read_parquet('{tmp_path}/scores/*/*.parquet', hive_partitioning=true) "
        "ORDER BY assay_id, mutant"
    ).fetchall()
    assert rows == [
        ("AS1_HUMAN", "K2A:V3A", -0.5, 0, None),  # empty score -> NULL, multi-mutants kept
        ("AS1_HUMAN", "M1A", 0.5, 1, -1.25),
        ("AS2_YEAST", "G1C", 1.5, 1, 0.25),
    ]
    part = tmp_path / "scores" / "assay_id=AS1_HUMAN" / "data.parquet"
    columns = duckdb.sql(f"SELECT * FROM read_parquet('{part}', hive_partitioning=false)")
    assert columns.columns == ["mutant", "DMS_score", "DMS_score_bin", "ESM2_8M"]

    # Second run: the converted output stands in for the deleted archive; no network.
    assert run(rf, server, entry) == entry
    assert len(server.requests) == 1


def test_conversion_fails_naming_assay_with_missing_column(tmp_path: Path, run: RunFn) -> None:
    payload = make_zip({**SCORES, "AS3_MOUSE.csv": b"mutant,DMS_score\nA1C,0.1\n"})
    rf = remote("scores.zip", payload, archive=True, to_parquet=CONVERSION)
    with pytest.raises(ArchiveContentError, match=r"AS3_MOUSE.*DMS_score_bin"):
        run(rf, Server({"scores.zip": payload}), None)
    assert not (tmp_path / "scores").exists()
    assert (tmp_path / "scores.zip").exists()  # kept, so a rerun need not re-download


def test_conversion_fails_on_uncastable_value(run: RunFn) -> None:
    bad = b"mutant,DMS_score,DMS_score_bin,ESM2_8M\nM1A,high,1,0.1\n"
    payload = make_zip({"AS1_HUMAN.csv": bad})
    rf = remote("scores.zip", payload, archive=True, to_parquet=CONVERSION)
    with pytest.raises(ArchiveContentError, match="AS1_HUMAN"):
        run(rf, Server({"scores.zip": payload}), None)


def test_fetch_release_writes_manifest_and_is_idempotent(tmp_path: Path) -> None:
    structures = make_zip({"s/A.pdb": b"pdb"})
    files = (remote("ref.csv", CSV), remote("s.zip", structures, archive=True))
    release = Release("v9", tmp_path / "raw", tmp_path / "manifest.yaml", files)
    server = Server({"ref.csv": CSV, "s.zip": structures})

    with server.client() as client:
        fetch_release(release, client)
        fetch_release(release, client)

    assert len(server.requests) == 2
    manifest = Manifest.load(release.manifest_path)
    entry = manifest.get("proteingym_v9", "ref.csv")
    assert entry is not None
    assert entry.sha256 == hashlib.sha256(CSV).hexdigest()
    assert manifest.get("proteingym_v9", "s.zip") is not None
