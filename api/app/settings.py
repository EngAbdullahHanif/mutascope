"""API settings (pydantic-settings): environment variables ``MUTASCOPE_API_<FIELD>``.

The defaults are the pins in ``conf/`` (a test fails if they drift apart): the website bundle at
``export.publish.revision`` of the dataset repo (ADR 0028) and the fp32 ONNX models at
``onnx.publish.revision`` (ADR 0029, section 6: int8 is rejected). Both are read by commit
hash, never by branch, so a later push cannot change what a running API serves.
"""

import tempfile
from pathlib import Path
from typing import Annotated, Literal

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

    score_default_model: Literal["esm2_35m", "esm2_150m"] = "esm2_150m"
    """``POST /v1/score`` without ``model``. ADR 0029's rule: 150M, unless its fast-mode p95 on
    the Space exceeds 2 s at 1,000 residues; then 35M (set this, no code change)."""
    precise_max_length: int = Field(default=600, ge=1, le=1022)
    """Longest sequence a precise-mode job accepts: the masked marginal must stay under about
    3 minutes on the Space (ADR 0029, section 6). Provisional until the Space is measured:
    600 residues take 64 s on the M4 Pro at 2 threads, so the limit holds unless the Space is
    more than 2.8x slower (ADR 0031)."""
    job_queue_size: int = Field(default=4, ge=1)
    """Precise-mode jobs that may wait behind the running one; one more is a 429."""
    job_ttl_seconds: float = Field(default=3600, gt=0)
    """How long a finished job (and its result) stays readable."""
    score_cache_dir: Path = Path(tempfile.gettempdir()) / "mutascope-api-cache"
    """diskcache directory of live landscapes (fast and precise)."""
    score_cache_bytes: int = Field(default=256 * 2**20, ge=2**20)
    """Size limit of that cache; least recently used entries are evicted. A 1,022-residue
    landscape is 82 kB."""

    protein_cache_size: int = Field(default=32, ge=1)
    """Parsed protein files kept in memory (the largest is about 70k grid cells)."""
    cache_control: str = "public, max-age=3600"
    """``Cache-Control`` of the read-only ``/v1`` routes. Their content changes only with the
    pinned bundle, and the ETag lets a client revalidate cheaply after it expires."""
