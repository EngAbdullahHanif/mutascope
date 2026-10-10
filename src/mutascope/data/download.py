"""Download the pinned ProteinGym release, verify checksums, extract archives.

Idempotent: a file already on disk is re-hashed and must match the manifest; nothing is fetched
again. Every check failure raises instead of repairing, so a corrupt or changed upstream file can
never slip into the pipeline. The first successful fetch of a file is verified against the md5
published upstream and its sha256 is recorded in the manifest; later runs verify that sha256.
"""

import shutil
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

import duckdb
import httpx

from mutascope.data.manifest import Manifest, ManifestEntry
from mutascope.data.release import ParquetConversion, Release, RemoteFile
from mutascope.utils.hashing import CHUNK_SIZE, DigestAccumulator, Digests, file_digests
from mutascope.utils.io import fresh_tmp_dir
from mutascope.utils.logging import get_logger

log = get_logger(__name__)


class DownloadError(RuntimeError):
    """A file could not be fetched or failed verification."""


class ArchiveContentError(DownloadError):
    """An archive's contents are not what the pipeline expects."""


def fetch_release(release: Release, client: httpx.Client) -> None:
    """Make every file of ``release`` present and verified under ``release.raw_dir``.

    The manifest is saved after each file, so an interrupted run keeps the checksums of
    everything it already verified.
    """
    manifest = Manifest.load(release.manifest_path)
    release.raw_dir.mkdir(parents=True, exist_ok=True)
    for remote in release.files:
        entry = ensure_file(
            remote, release.raw_dir, client, manifest.get(release.manifest_key, remote.filename)
        )
        manifest.record(release.manifest_key, remote.filename, entry)
        manifest.save()
    log.info("release_ready", version=release.version, raw_dir=str(release.raw_dir))


def ensure_file(
    remote: RemoteFile,
    raw_dir: Path,
    client: httpx.Client,
    recorded: ManifestEntry | None,
) -> ManifestEntry:
    """Download (if needed), verify and unpack one file; return its manifest entry."""
    dest = raw_dir / remote.filename
    out_dir = raw_dir / remote.extract_dirname
    conversion = remote.to_parquet

    if conversion is not None and conversion.delete_archive and out_dir.exists():
        # The archive was deliberately deleted after conversion; its checksum is in the manifest.
        if recorded is None:
            raise DownloadError(
                f"{out_dir} exists but {remote.filename} has no manifest entry; "
                "delete the directory to re-download and re-verify"
            )
        log.info("skip_converted", file=remote.filename, out_dir=str(out_dir))
        return recorded

    entry = _ensure_downloaded(remote, dest, client, recorded)

    if remote.archive and not out_dir.exists():
        if conversion is None:
            extract_archive(dest, out_dir)
        else:
            convert_archive_to_parquet(dest, out_dir, conversion)
    if conversion is not None and conversion.delete_archive:
        dest.unlink()
        log.info("archive_deleted", file=remote.filename)
    return entry


def _ensure_downloaded(
    remote: RemoteFile, dest: Path, client: httpx.Client, recorded: ManifestEntry | None
) -> ManifestEntry:
    if dest.exists():
        digests = file_digests(dest)
        _verify(remote, digests, recorded, source=f"local file {dest} (delete it to re-download)")
        log.info("verified_existing", file=remote.filename)
        return _entry(remote, digests)

    # Stream to a sibling .part file and rename only after verification: `dest` existing
    # therefore always means "complete and verified at least once".
    part = dest.with_name(dest.name + ".part")
    try:
        digests = _stream_to_file(client, remote, part)
        _verify(remote, digests, recorded, source=f"download from {remote.url}")
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.replace(dest)
    return _entry(remote, digests)


def _stream_to_file(client: httpx.Client, remote: RemoteFile, path: Path) -> Digests:
    acc = DigestAccumulator()
    start = time.monotonic()
    next_report = 0.1
    log.info("download_start", file=remote.filename, url=remote.url, mb=remote.size_bytes / 1e6)
    with client.stream("GET", remote.url) as response, path.open("wb") as fh:
        response.raise_for_status()
        for chunk in response.iter_bytes(CHUNK_SIZE):
            fh.write(chunk)
            acc.update(chunk)
            done = acc.result().size_bytes / remote.size_bytes
            if done >= next_report:
                log.info("download_progress", file=remote.filename, pct=round(100 * done))
                next_report += 0.1
    digests = acc.result()
    elapsed = time.monotonic() - start
    log.info("download_done", file=remote.filename, seconds=round(elapsed, 1))
    return digests


def _verify(
    remote: RemoteFile, digests: Digests, recorded: ManifestEntry | None, *, source: str
) -> None:
    problems: list[str] = []
    if digests.size_bytes != remote.size_bytes:
        problems.append(f"size {digests.size_bytes} != expected {remote.size_bytes}")
    if remote.md5 is not None and digests.md5 != remote.md5:
        problems.append(f"md5 {digests.md5} != published {remote.md5}")
    if recorded is not None and digests.sha256 != recorded.sha256:
        problems.append(f"sha256 {digests.sha256} != manifest {recorded.sha256}")
    if problems:
        log.error("checksum_mismatch", file=remote.filename, source=source, problems=problems)
        raise DownloadError(
            f"{remote.filename}: {source} failed verification: {'; '.join(problems)}"
        )


def _entry(remote: RemoteFile, digests: Digests) -> ManifestEntry:
    return ManifestEntry(
        url=remote.url, size_bytes=digests.size_bytes, md5=digests.md5, sha256=digests.sha256
    )


def _is_junk(member: zipfile.ZipInfo) -> bool:
    """Directories and macOS metadata (``.DS_Store``, ``__MACOSX/``) that zips often carry."""
    parts = PurePosixPath(member.filename).parts
    return member.is_dir() or "__MACOSX" in parts or parts[-1].startswith(".")


def _members(archive: zipfile.ZipFile) -> list[tuple[str, zipfile.ZipInfo]]:
    """Content members keyed by basename; refuses archives where two files share one.

    Using only the basename both flattens the archive's top-level folder and makes path traversal
    (``../``) in member names impossible.
    """
    by_name: dict[str, zipfile.ZipInfo] = {}
    for member in archive.infolist():
        if _is_junk(member):
            continue
        name = PurePosixPath(member.filename).name
        if name in by_name:
            raise ArchiveContentError(f"duplicate file name {name!r} in archive")
        by_name[name] = member
    return sorted(by_name.items())


def extract_archive(archive_path: Path, out_dir: Path) -> None:
    """Extract an archive's files (flattened to basenames) into ``out_dir``.

    Extracts into a temporary sibling and renames at the end, so ``out_dir`` existing means the
    extraction completed.
    """
    tmp = fresh_tmp_dir(out_dir)
    with zipfile.ZipFile(archive_path) as archive:
        members = _members(archive)
        for name, member in members:
            with archive.open(member) as src, (tmp / name).open("wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK_SIZE)
    tmp.replace(out_dir)
    log.info("extracted", archive=archive_path.name, files=len(members), out_dir=str(out_dir))


def convert_archive_to_parquet(
    archive_path: Path, out_dir: Path, conversion: ParquetConversion
) -> None:
    """Write the configured columns of every CSV in the archive to ``out_dir/assay_id=<id>/``.

    Each CSV is staged to disk one at a time (DuckDB cannot read zip members directly), read as
    text and cast to the configured types, so peak extra disk use is a single CSV.
    """
    tmp = fresh_tmp_dir(out_dir)
    wanted = [name for name, _ in conversion.columns]
    select = ", ".join(
        f"CAST({_quote(name)} AS {dtype}) AS {_quote(name)}" for name, dtype in conversion.columns
    )
    with (
        zipfile.ZipFile(archive_path) as archive,
        tempfile.TemporaryDirectory() as staging,
        duckdb.connect() as con,
    ):
        members = [(n, m) for n, m in _members(archive) if n.endswith(".csv")]
        for name, member in members:
            assay_id = name.removesuffix(".csv")
            staged = Path(staging) / name
            with archive.open(member) as src, staged.open("wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK_SIZE)
            rel = con.read_csv(str(staged), header=True, all_varchar=True)
            missing = [c for c in wanted if c not in rel.columns]
            if missing:
                raise ArchiveContentError(f"assay {assay_id}: missing columns {missing}")
            part_dir = tmp / f"assay_id={assay_id}"
            part_dir.mkdir()
            try:
                rel.select(select).write_parquet(str(part_dir / "data.parquet"))
            except duckdb.Error as exc:
                raise ArchiveContentError(f"assay {assay_id}: {exc}") from exc
            staged.unlink()
    tmp.replace(out_dir)
    log.info("converted", archive=archive_path.name, assays=len(members), out_dir=str(out_dir))


def _quote(identifier: str) -> str:
    """Quote a column name for SQL (names like ``ESMC-300M`` are not bare identifiers)."""
    return '"' + identifier.replace('"', '""') + '"'
