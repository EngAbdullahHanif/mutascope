"""API settings (pydantic-settings): environment variables ``MUTASCOPE_API_<FIELD>``.

The defaults are the pins in ``conf/`` (a test fails if they drift apart): the website bundle at
``export.publish.revision`` of the dataset repo (ADR 0028) and the fp32 ONNX models at
``onnx.publish.revision`` (ADR 0029, section 6: int8 is rejected). Both are read by commit
hash, never by branch, so a later push cannot change what a running API serves.
"""

from pathlib import Path
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CommitHash = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Everything the API reads at startup."""

    model_config = SettingsConfigDict(env_prefix="MUTASCOPE_API_", frozen=True)

    bundle_repo: str = "engAbdullahHanif/mutascope-proteingym-v1.3"
    """Hub dataset repo holding the bundle (``publish.repo_id``)."""
    bundle_path: str = "bundle"
    """Folder of the bundle in that repo (``export.publish.path_in_repo``)."""
    bundle_revision: CommitHash = "3ae767217bb0a56b8f2e7c79c68b73119e308066"
    """Commit of ``bundle-v1.0.0`` (``export.publish.revision``)."""
    bundle_dir: Path | None = None
    """Read a local copy of the bundle instead of the Hub (tests, development). It must hold
    ``SHA256SUMS``, ``manifest.json`` and ``benchmarks.json``; protein files are checked
    against the manifest when read."""

    onnx_models: tuple[str, ...] = ("esm2_35m_onnx_fp32", "esm2_150m_onnx_fp32")
    """Model configs (``conf/model/<name>.yaml``) served live; each must be fp32."""
    onnx_revision: CommitHash = "82e15287c50f45f771cca75ac3786f0376ecabb2"
    """Commit of the ONNX model repo (``onnx.publish.revision``)."""
    onnx_threads: int = Field(default=2, ge=1)
    """ONNX Runtime intra-op threads per model (the free Space has 2 vCPUs)."""
    onnx_local_dir: Path | None = None
    """Read the ONNX files from an export directory instead of the Hub (tests)."""

    config_dir: Path = REPO_ROOT / "conf"
    """Hydra config root: the ONNX model configs name their files there."""

    protein_cache_size: int = Field(default=32, ge=1)
    """Parsed protein files kept in memory (the largest is about 70k grid cells)."""
    cache_control: str = "public, max-age=3600"
    """``Cache-Control`` of the read-only ``/v1`` routes. Their content changes only with the
    pinned bundle, and the ETag lets a client revalidate cheaply after it expires."""
