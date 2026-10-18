"""API settings (pydantic-settings): environment variables ``MUTASCOPE_API_<FIELD>``.

The defaults are the pins in ``conf/`` (a test fails if they drift apart): the website bundle at
``export.publish.revision`` of the dataset repo (ADR 0028) and the fp32 ONNX models at
``onnx.publish.revision`` (ADR 0029, section 6: int8 is rejected). Both are read by commit
hash, never by branch, so a later push cannot change what a running API serves.
"""

import tempfile
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
from typing import Annotated, Any, Literal

from limits import parse_many
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CommitHash = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
IPNetwork = IPv4Network | IPv6Network

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Everything the API reads at startup."""

    model_config = SettingsConfigDict(env_prefix="MUTASCOPE_API_", frozen=True)

    host: str = "0.0.0.0"
    """Address the start command (``python -m api.app.serve``) binds."""
    port: int = Field(default=7860, ge=1, le=65535)
    """Port of the start command (the Space's port)."""
    max_connections: int = Field(default=200, ge=10)
    """Concurrent connections uvicorn accepts (``limit_concurrency``); more get a 503. Bounds
    sockets and tasks however many addresses a client has."""
    lock_file: Path = Path(tempfile.gettempdir()) / "mutascope-api.lock"
    """Held for the server's lifetime: a second process on this host refuses to start, because
    the job queue and the rate limiter live in one process's memory (ADR 0032)."""

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
    inference_concurrency: int = Field(default=1, ge=1)
    """Forward passes (fast and precise) that run at once (:mod:`api.app.inference`). One run
    already uses ``onnx_threads`` cores; more only share the same CPU and make reads wait."""
    inference_wait_seconds: float = Field(default=10, gt=0)
    """How long a fast-mode request waits for a free slot before it is a 503 with
    ``Retry-After`` (this, rounded up). Precise-mode batches wait without a limit."""
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

    # -- hardening (ADR 0032) -------------------------------------------------------------------

    rate_limit_read: str = "120/minute"
    """Per client, every read route: ``/v1/proteins*``, ``/v1/benchmarks``, ``GET`` / ``DELETE
    /v1/jobs/{id}`` and opening a stream. Syntax of the ``limits`` package (``;`` joins limits)."""
    rate_limit_score: str = "20/minute"
    """Per client, ``POST /v1/score`` (fast mode; cache hits count)."""
    rate_limit_score_global: str = "120/minute"
    """All clients together, ``POST /v1/score``: about what 2 vCPUs can score at 1,000 residues
    with 150M (0.6 s a request on the M4 Pro), so many addresses cannot add up to more."""
    rate_limit_jobs: str = "2/minute;10/hour"
    """Per client, ``POST /v1/jobs`` (precise mode; a cached job counts too)."""
    max_active_jobs_per_client: int = Field(default=1, ge=1)
    """Queued or running jobs one client may have; another is a 429. Without it, one client
    could fill the whole queue (``job_queue_size``) within the job rate limit."""
    max_streams_per_client: int = Field(default=4, ge=1)
    """Open SSE streams per client; another is a 429 until one closes."""
    max_streams_total: int = Field(default=64, ge=1)
    """Open SSE streams over all clients."""
    trusted_proxies: Annotated[tuple[IPNetwork, ...], NoDecode] = ()
    """Addresses or networks of reverse proxies (the Space's) whose ``X-Forwarded-For`` is
    believed. Comma-separated in the environment. Empty: the socket address is the client."""
    cors_origins: Annotated[tuple[str, ...], NoDecode] = ("http://localhost:3000",)
    """Browser origins allowed to call the API: the production site (set at deploy) and the
    Next.js dev server. Comma-separated in the environment."""
    max_body_bytes: int = Field(default=16 * 1024, ge=1024)
    """Largest request body; more is a 413. The largest valid body (1,022 residues and 50
    mutations) is 1.8 kB."""

    metrics_token: SecretStr | None = Field(default=None, min_length=32)
    """Bearer token of ``GET /metrics``; without one the route answers 404."""
    sentry_dsn: SecretStr | None = None
    """Sentry is enabled only when this is set."""
    sentry_environment: str = "development"
    sentry_traces_sample_rate: float = Field(default=0.0, ge=0, le=1)

    @field_validator("metrics_token", "sentry_dsn", mode="before")
    @classmethod
    def _strip_secret(cls, value: Any) -> Any:
        # A secret written with `echo` (Secret Manager, a .env file) ends in a newline; kept, it
        # would make every bearer token a mismatch.
        return value.strip() if isinstance(value, str) else value

    @field_validator("trusted_proxies", "cors_origins", mode="before")
    @classmethod
    def _split_commas(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _networks(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            items: list[Any] = list(value)  # pyright: ignore[reportUnknownArgumentType]
            return tuple(ip_network(v, strict=False) if isinstance(v, str) else v for v in items)
        return value

    @field_validator(
        "rate_limit_read", "rate_limit_score", "rate_limit_score_global", "rate_limit_jobs"
    )
    @classmethod
    def _limits(cls, value: str) -> str:
        parse_many(value)  # a typo fails at startup, not at the first request
        return value
