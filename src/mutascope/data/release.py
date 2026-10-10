"""Typed view of the pinned ProteinGym release (``conf/data/proteingym.yaml``)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from omegaconf import DictConfig, OmegaConf

from mutascope.utils.config import as_str_dict

T = TypeVar("T")


class ReleaseConfigError(ValueError):
    """The data config is missing a field or has one of the wrong type."""


@dataclass(frozen=True)
class ParquetConversion:
    """Keep only ``columns`` of every CSV in an archive, as Parquet partitioned by assay_id."""

    columns: tuple[tuple[str, str], ...]
    """(column name, DuckDB type) pairs, in output order."""
    delete_archive: bool


@dataclass(frozen=True)
class RemoteFile:
    """One file of the release, with the size and checksum published upstream."""

    key: str
    filename: str
    url: str
    size_bytes: int
    md5: str | None
    archive: bool
    to_parquet: ParquetConversion | None = None

    @property
    def extract_dirname(self) -> str:
        """Directory (under the raw dir) an archive is extracted or converted into."""
        return Path(self.filename).stem


@dataclass(frozen=True)
class Release:
    """A pinned ProteinGym release and where it lives locally."""

    version: str
    raw_dir: Path
    manifest_path: Path
    files: tuple[RemoteFile, ...]
    structure_exceptions: dict[str, str] = field(default_factory=dict[str, str])
    """assay_id -> documented reason its structure mapping is known to fail."""

    @property
    def manifest_key(self) -> str:
        """Top-level key for this release in the shared manifest file."""
        return f"proteingym_{self.version}"

    def file(self, key: str) -> RemoteFile:
        """Look up a file by its config key (e.g. ``"reference"``)."""
        for f in self.files:
            if f.key == key:
                return f
        raise KeyError(key)


def load_release(data_cfg: DictConfig) -> Release:
    """Build a :class:`Release` from the ``data`` config group, resolving interpolations."""
    cfg = _mapping({"data": OmegaConf.to_container(data_cfg, resolve=True)}, "data")
    files = _mapping(cfg, "files")
    return Release(
        version=str(_get(_mapping(cfg, "release"), "version", str)),
        raw_dir=Path(_get(cfg, "raw_dir", str)),
        manifest_path=Path(_get(cfg, "manifest_path", str)),
        files=tuple(_remote_file(key, _mapping(files, key)) for key in files),
        structure_exceptions=_structure_exceptions(cfg),
    )


def _structure_exceptions(cfg: Mapping[str, Any]) -> dict[str, str]:
    if cfg.get("structure_exceptions") is None:
        return {}
    exceptions = _mapping(cfg, "structure_exceptions")
    for assay_id, reason in exceptions.items():
        if not isinstance(reason, str) or not reason.strip():
            raise ReleaseConfigError(f"structure_exceptions.{assay_id} needs a non-empty reason")
    return {assay_id: str(reason).strip() for assay_id, reason in exceptions.items()}


def _remote_file(key: str, spec: Mapping[str, Any]) -> RemoteFile:
    md5 = spec.get("md5")
    if md5 is not None and not isinstance(md5, str):
        raise ReleaseConfigError(f"files.{key}.md5 must be a string or null")
    conversion = None
    if spec.get("to_parquet") is not None:
        conv = _mapping(spec, "to_parquet")
        columns = _mapping(conv, "columns")
        if not columns or not all(isinstance(t, str) for t in columns.values()):
            raise ReleaseConfigError(
                f"files.{key}.to_parquet.columns must be a non-empty column -> type mapping"
            )
        conversion = ParquetConversion(
            columns=tuple((name, str(dtype)) for name, dtype in columns.items()),
            delete_archive=_get(conv, "delete_archive", bool),
        )
    archive = _get(spec, "archive", bool)
    if conversion is not None and not archive:
        raise ReleaseConfigError(f"files.{key}: to_parquet requires archive: true")
    return RemoteFile(
        key=key,
        filename=_get(spec, "filename", str),
        url=_get(spec, "url", str),
        size_bytes=_get(spec, "size_bytes", int),
        md5=md5,
        archive=archive,
        to_parquet=conversion,
    )


def _mapping(cfg: Mapping[str, Any], key: str) -> dict[str, Any]:
    try:
        return as_str_dict(cfg.get(key), key)
    except TypeError as exc:
        raise ReleaseConfigError(str(exc)) from exc


def _get(cfg: Mapping[str, Any], key: str, kind: type[T]) -> T:
    value = cfg.get(key)
    if not isinstance(value, kind):
        raise ReleaseConfigError(f"{key} must be {kind.__name__}, got {value!r}")
    return value
