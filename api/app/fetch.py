"""Fetch what the API serves at the pinned revisions and verify it (``python -m api.app.fetch``).

The API image runs this at build time (ADR 0033), so the container starts offline
(``HF_HUB_OFFLINE=1``) from files that were checked once, when the image was made:

- the website bundle at ``bundle_revision``: every file in its ``SHA256SUMS``, the manifest's
  protein files and the schema version, exactly as the API's startup checks them
  (:func:`api.app.bundle.open_bundle`; the startup check runs again in the container);
- the fp32 ONNX graph and ``tokenizer.json`` of every served model at ``onnx_revision``: each
  against the ONNX repo's ``SHA256SUMS`` of that commit. A file the list does not name is an
  error, not a pass.

Both go into the Hugging Face cache (``HF_HOME``), where the scorers and the bundle loader find
them by the same revision at startup. The checksum lists are read from the pinned commits
themselves, so a commit hash fixes the files and the digests they are checked against.
"""

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from api.app.bundle import CHECKSUMS, open_bundle
from api.app.logs import configure_api_logging
from api.app.scorers import model_config
from api.app.settings import Settings
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

FileDownload = Callable[[str, str, str], Path]
"""``(repo_id, filename, revision) -> local path`` (``hf_hub_download`` of a model repo)."""


class FetchError(RuntimeError):
    """A file is missing from the checksum list or its digest differs."""


@dataclass(frozen=True)
class OnnxFiles:
    """The files one served model needs, as named in its config."""

    model: str
    repo: str
    files: tuple[str, ...]


def _hf_hub_download(repo_id: str, filename: str, revision: str) -> Path:
    from huggingface_hub import hf_hub_download  # pyright: ignore[reportUnknownVariableType]

    return Path(hf_hub_download(repo_id, filename, revision=revision))


def sha256_file(path: Path, chunk: int = 2**20) -> str:
    """Digest of a file read in chunks: the 150M graph is 594 MB."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def parse_checksums(text: str) -> dict[str, str]:
    """``SHA256SUMS`` (``<digest>  <path>`` per line) as ``{path: digest}``."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            sums[name.strip()] = digest
    return sums


def served_onnx_files(settings: Settings) -> list[OnnxFiles]:
    """The graph and tokenizer of each model in ``settings.onnx_models``, from its config.

    ``model_config`` refuses a config that is not an fp32 ONNX scorer, so nothing else can be
    baked into the image.
    """
    out: list[OnnxFiles] = []
    for name in settings.onnx_models:
        cfg = model_config(
            settings.config_dir,
            name,
            revision=settings.onnx_revision,
            threads=settings.onnx_threads,
            local_dir=None,
        )
        out.append(
            OnnxFiles(
                model=name,
                repo=str(cfg["hf_id"]),
                files=(str(cfg["file"]), str(cfg["tokenizer_file"])),
            )
        )
    return out


def verify_files(paths: Mapping[str, Path], sums: Mapping[str, str]) -> None:
    """Check each downloaded file against its digest in ``sums``.

    Raises:
        FetchError: A file is not listed, or its digest differs.
    """
    for name, path in paths.items():
        expected = sums.get(name)
        if expected is None:
            raise FetchError(f"{name} is not listed in {CHECKSUMS}")
        if sha256_file(path) != expected:
            raise FetchError(f"{name}: sha256 differs from {CHECKSUMS}")


def fetch_onnx(settings: Settings, download: FileDownload = _hf_hub_download) -> int:
    """Download and verify the served ONNX files; return how many were checked."""
    checked = 0
    sums_by_repo: dict[str, dict[str, str]] = {}
    for model in served_onnx_files(settings):
        if model.repo not in sums_by_repo:
            sums_path = download(model.repo, CHECKSUMS, settings.onnx_revision)
            sums_by_repo[model.repo] = parse_checksums(sums_path.read_text())
        paths = {name: download(model.repo, name, settings.onnx_revision) for name in model.files}
        verify_files(paths, sums_by_repo[model.repo])
        checked += len(paths)
        log.info(
            "onnx_files_verified",
            model=model.model,
            repo=model.repo,
            revision=settings.onnx_revision[:7],
            files=list(paths),
        )
    return checked


def fetch_all(settings: Settings) -> None:
    """The bundle and the ONNX files at their pinned revisions, all verified."""
    bundle = open_bundle(
        repo=settings.bundle_repo,
        path_in_repo=settings.bundle_path,
        revision=settings.bundle_revision,
        local_dir=None,
        cache_size=1,
    )
    n_onnx = fetch_onnx(settings)
    log.info(
        "fetch_complete",
        bundle_revision=settings.bundle_revision[:7],
        proteins=len(bundle.entries),
        onnx_revision=settings.onnx_revision[:7],
        onnx_files=n_onnx,
    )


def main() -> None:
    configure_api_logging()
    fetch_all(Settings())


if __name__ == "__main__":
    main()
