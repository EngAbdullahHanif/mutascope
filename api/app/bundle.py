"""The website bundle (ADR 0028) as the API reads it: pinned, checksum-verified, schema-checked.

At startup :func:`fetch_bundle` downloads the bundle folder of the dataset repo at the pinned
commit, and :func:`open_bundle` checks it before anything is served:

- every file in the folder's ``SHA256SUMS`` (published in the same commit) has that digest;
- ``manifest.json`` and ``benchmarks.json`` declare a schema version this code reads
  (:data:`SUPPORTED_SCHEMA_VERSIONS`); otherwise the API refuses to start, rather than serve
  numbers it might misread.

Protein files are parsed when first requested (the 217 of them are 79 MB of JSON) and kept in
an LRU cache; each is checked against the manifest's sha256 when read. The API never reads
``data/processed``: everything it serves is in these files.
"""

import gzip
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from mutascope.export.format import (
    SCHEMA_VERSION,
    Benchmarks,
    Manifest,
    ManifestEntry,
    ProteinFile,
)
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})
"""Bundle schema versions the response models are built from. The bundle's Pydantic models
forbid unknown fields, so even a minor version is only read once it is listed here."""
CHECKSUMS = "SHA256SUMS"
MANIFEST = "manifest.json"
BENCHMARKS = "benchmarks.json"


class BundleError(RuntimeError):
    """The bundle cannot be served: missing file, checksum mismatch, unsupported schema."""


@dataclass(frozen=True)
class Source:
    """Where the bundle was read from, as reported by ``/ready`` and the version stamp."""

    repo: str | None
    revision: str | None
    path: Path


SnapshotDownload = Callable[..., str]


def _snapshot_download(
    *, repo_id: str, repo_type: str, revision: str, allow_patterns: list[str]
) -> str:
    from huggingface_hub import (
        snapshot_download,  # pyright: ignore[reportUnknownVariableType]
    )

    return str(
        snapshot_download(
            repo_id, repo_type=repo_type, revision=revision, allow_patterns=allow_patterns
        )
    )


def fetch_bundle(
    repo: str, path_in_repo: str, revision: str, download: SnapshotDownload = _snapshot_download
) -> Source:
    """Download the bundle folder of ``repo`` (a dataset repo) at commit ``revision``.

    Raises:
        BundleError: ``revision`` is not a full commit hash.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise BundleError(f"bundle revision {revision!r} is not a full commit hash")
    root = download(
        repo_id=repo,
        repo_type="dataset",
        revision=revision,
        allow_patterns=[f"{path_in_repo}/*", f"{path_in_repo}/**/*"],
    )
    log.info("bundle_downloaded", repo=repo, revision=revision[:7], path=path_in_repo)
    return Source(repo=repo, revision=revision, path=Path(root) / path_in_repo)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_checksums(root: Path, *, require_all: bool) -> int:
    """Check the files listed in ``root/SHA256SUMS``; return how many were checked.

    ``manifest.json`` and ``benchmarks.json`` must always be present. With ``require_all``
    (a Hub snapshot) every listed file must be; a local copy may hold a subset of the
    protein files, which are checked against the manifest when read.

    Raises:
        BundleError: A required file is missing or a digest differs.
    """
    sums = root / CHECKSUMS
    if not sums.is_file():
        raise BundleError(f"{sums} is missing")
    checked = 0
    for line in sums.read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        path = root / name
        if not path.is_file():
            if require_all or name in (MANIFEST, BENCHMARKS):
                raise BundleError(f"{name} is listed in {CHECKSUMS} but missing")
            continue
        if _sha256(path) != digest:
            raise BundleError(f"{name}: sha256 differs from {CHECKSUMS}")
        checked += 1
    return checked


def _read_versioned(path: Path) -> dict[str, Any]:
    """Parse a bundle JSON file, refusing a schema version this code does not read."""
    data: dict[str, Any] = json.loads(path.read_text())
    version = data.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise BundleError(
            f"{path.name} has schema version {version!r}; this API reads "
            f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    return data


class Bundle:
    """A verified bundle: manifest and benchmarks in memory, protein files on demand."""

    def __init__(self, source: Source, *, cache_size: int, require_all: bool) -> None:
        root = source.path
        n_checked = verify_checksums(root, require_all=require_all)
        self.source = source
        self.manifest = Manifest.model_validate(_read_versioned(root / MANIFEST))
        self.benchmarks = Benchmarks.model_validate(_read_versioned(root / BENCHMARKS))
        self.entries: dict[str, ManifestEntry] = {p.assay_id: p for p in self.manifest.proteins}
        if require_all:
            missing = [e.file for e in self.entries.values() if not (root / e.file).is_file()]
            if missing:
                raise BundleError(f"{len(missing)} protein files missing, e.g. {missing[0]}")
        self.protein = lru_cache(maxsize=cache_size)(self._read_protein)
        log.info(
            "bundle_opened",
            path=str(root),
            revision=source.revision,
            schema_version=self.manifest.schema_version,
            proteins=len(self.entries),
            files_checked=n_checked,
        )

    @property
    def schema_version(self) -> str:
        return self.manifest.schema_version

    def _read_protein(self, assay_id: str) -> ProteinFile:
        """Parse one protein file after checking it against the manifest.

        Raises:
            KeyError: ``assay_id`` is not in the manifest.
            BundleError: The file is missing, differs from the manifest or has another schema.
        """
        entry = self.entries[assay_id]
        path = self.source.path / entry.file
        if not path.is_file():
            raise BundleError(f"{entry.file} is missing")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry.sha256:
            raise BundleError(f"{entry.file}: sha256 differs from the manifest")
        data: dict[str, Any] = json.loads(gzip.decompress(raw))
        if data.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            raise BundleError(f"{entry.file} has schema version {data.get('schema_version')!r}")
        return ProteinFile.model_validate(data)


def open_bundle(
    *,
    repo: str,
    path_in_repo: str,
    revision: str,
    local_dir: Path | None,
    cache_size: int,
    download: SnapshotDownload = _snapshot_download,
) -> Bundle:
    """The pinned Hub bundle, or the local copy at ``local_dir`` when one is given."""
    if local_dir is not None:
        log.warning("bundle_local_copy", path=str(local_dir))
        source = Source(repo=None, revision=None, path=local_dir)
        return Bundle(source, cache_size=cache_size, require_all=False)
    source = fetch_bundle(repo, path_in_repo, revision, download)
    return Bundle(source, cache_size=cache_size, require_all=True)
