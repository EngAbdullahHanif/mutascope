"""Deploy a released API image to Google Cloud Run, then wait until it serves (ADR 0034).

Usage (CI's release workflow)::

    python -m api.deploy.cloudrun outputs >> "$GITHUB_OUTPUT"
    python -m api.deploy.cloudrun render --tag v0.2.0 --digest sha256:<64 hex> --out service.json
    gcloud run services replace service.json --region <region> --project <project>
    python -m api.deploy.cloudrun wait --tag v0.2.0 --url https://<service>.run.app

The service is not configured in the console: ``render`` writes the whole Knative service
(resources, scaling, probes, environment, the image by digest) from ``conf/deploy/cloudrun.yaml``,
and ``gcloud run services replace`` makes Cloud Run's service exactly that. A setting changed by
hand is undone by the next deploy, and every change is a reviewed diff.

The image runs by digest, so Cloud Run runs the bytes CI built and tested even if the tag is
moved later; the tag is kept as a revision label. ``wait`` passes only when ``/health`` reports
the tag's version and ``/ready`` answers 200 at the service's public URL.
"""

import argparse
import json
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import httpx
from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict, Field

from mutascope.utils.logging import configure_logging, get_logger

log = get_logger(__name__)

CONFIG = Path(__file__).resolve().parents[2] / "conf" / "deploy" / "cloudrun.yaml"
PORT = 7860
"""The image's port (ADR 0033); Cloud Run sends traffic and probes there."""
TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class DeployError(RuntimeError):
    """The deploy could not be made, or the service did not come up serving the release."""


class WaitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    timeout_seconds: float = Field(gt=0)
    interval_seconds: float = Field(gt=0)


class CloudRunConfig(BaseModel):
    """``conf/deploy/cloudrun.yaml``."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: str
    project_number: str
    region: str
    service: str = Field(pattern=r"^[a-z][a-z0-9-]{0,48}$")
    artifact_repository: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    image_name: str = Field(pattern=r"^[a-z0-9./_-]+$")
    ghcr_image: str = Field(pattern=r"^ghcr\.io/[a-z0-9./_-]+$")
    runtime_service_account: str
    deployer_service_account: str
    workload_identity_pool: str
    workload_identity_provider: str
    cpu: str
    memory: str
    min_instances: int = Field(ge=0)
    max_instances: int = Field(ge=1, le=1)  # one process (ADR 0032)
    concurrency: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1, le=3600)
    env: dict[str, str]
    secrets: dict[str, str]
    wait: WaitConfig

    def require_project(self) -> None:
        """The project fields are empty until the one-time setup has run."""
        if not self.project_id or not self.project_number.isdigit():
            raise DeployError(
                "conf/deploy/cloudrun.yaml has no project_id / project_number yet: run "
                "tools/deploy/setup-gcp.sh once and copy the values it prints"
            )

    def account(self, name: str) -> str:
        return f"{name}@{self.project_id}.iam.gserviceaccount.com"

    @property
    def image(self) -> str:
        """The Artifact Registry image Cloud Run pulls."""
        return (
            f"{self.region}-docker.pkg.dev/{self.project_id}/"
            f"{self.artifact_repository}/{self.image_name}"
        )

    @property
    def provider(self) -> str:
        """Full resource name of the Workload Identity provider CI authenticates through."""
        return (
            f"projects/{self.project_number}/locations/global/workloadIdentityPools/"
            f"{self.workload_identity_pool}/providers/{self.workload_identity_provider}"
        )


def load_config(path: Path = CONFIG) -> CloudRunConfig:
    raw = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    return CloudRunConfig.model_validate(raw)


def version_of(tag: str) -> str:
    """``v0.2.0`` → ``0.2.0``: the package version the image's ``/health`` must report."""
    match = TAG.match(tag)
    if match is None:
        raise DeployError(f"not a release tag: {tag!r} (expected vMAJOR.MINOR.PATCH)")
    return match.group(1)


def service_manifest(cfg: CloudRunConfig, tag: str, digest: str) -> dict[str, Any]:
    """The whole Cloud Run service (Knative ``serving.knative.dev/v1``) for one release.

    - **Image by digest**, the tag as a label: the revision is immutable and still says which
      release it is.
    - **Startup probe on ``/ready``**: Cloud Run sends no traffic to a revision until the bundle
      and both models are loaded, and a revision that never gets ready never takes traffic, so
      a broken release leaves the previous one serving.
    - **Liveness on ``/health``**, every 30 s (the image's Docker ``HEALTHCHECK`` is ignored by
      Cloud Run).
    - **gen2** (full Linux: the lock file of ADR 0032), **startup CPU boost** (model loading),
      **request-based billing** (``cpu-throttling``): CPU is allocated while a request is in
      flight. A precise job advances while its client holds the SSE stream open, which is how
      the website follows it (ADR 0034).
    """
    cfg.require_project()
    version_of(tag)
    if DIGEST.match(digest) is None:
        raise DeployError(f"not an image digest: {digest!r}")
    env: list[dict[str, Any]] = [{"name": k, "value": v} for k, v in sorted(cfg.env.items())]
    env += [
        {"name": k, "valueFrom": {"secretKeyRef": {"name": secret, "key": "latest"}}}
        for k, secret in sorted(cfg.secrets.items())
    ]
    container: dict[str, Any] = {
        "image": f"{cfg.image}@{digest}",
        "ports": [{"name": "http1", "containerPort": PORT}],
        "resources": {"limits": {"cpu": cfg.cpu, "memory": cfg.memory}},
        "startupProbe": {
            "httpGet": {"path": "/ready", "port": PORT},
            # 10 s x 24 = 240 s, Cloud Run's limit for a startup probe; loading takes ~10-20 s.
            "periodSeconds": 10,
            "timeoutSeconds": 5,
            "failureThreshold": 24,
        },
        "livenessProbe": {
            "httpGet": {"path": "/health", "port": PORT},
            "periodSeconds": 30,
            "timeoutSeconds": 5,
            "failureThreshold": 3,
        },
    }
    if env:
        container["env"] = env
    return {
        "apiVersion": "serving.knative.dev/v1",
        "kind": "Service",
        "metadata": {
            "name": cfg.service,
            "labels": {"cloud.googleapis.com/location": cfg.region},
            "annotations": {"run.googleapis.com/ingress": "all"},
        },
        "spec": {
            "template": {
                "metadata": {
                    "labels": {"mutascope-release": tag.replace(".", "-")},
                    "annotations": {
                        "autoscaling.knative.dev/minScale": str(cfg.min_instances),
                        "autoscaling.knative.dev/maxScale": str(cfg.max_instances),
                        "run.googleapis.com/execution-environment": "gen2",
                        "run.googleapis.com/startup-cpu-boost": "true",
                        "run.googleapis.com/cpu-throttling": "true",
                    },
                },
                "spec": {
                    "containerConcurrency": cfg.concurrency,
                    "timeoutSeconds": cfg.timeout_seconds,
                    "serviceAccountName": cfg.account(cfg.runtime_service_account),
                    "containers": [container],
                },
            },
            "traffic": [{"percent": 100, "latestRevision": True}],
        },
    }


def github_outputs(cfg: CloudRunConfig) -> str:
    """``key=value`` lines for ``$GITHUB_OUTPUT``, so workflows read the config, not a copy."""
    cfg.require_project()
    values = {
        "project_id": cfg.project_id,
        "region": cfg.region,
        "service": cfg.service,
        "image": cfg.image,
        "registry": f"{cfg.region}-docker.pkg.dev",
        "ghcr_image": cfg.ghcr_image,
        "provider": cfg.provider,
        "deployer": cfg.account(cfg.deployer_service_account),
    }
    return "".join(f"{key}={value}\n" for key, value in values.items())


JsonGet = Callable[[str], tuple[int, dict[str, Any] | None]]
"""``url -> (status, JSON object or None)``; status 0 when the request did not complete."""


def _http_get(url: str) -> tuple[int, dict[str, Any] | None]:
    try:
        response = httpx.get(url, timeout=30, follow_redirects=True)
    except httpx.HTTPError:
        return 0, None
    try:
        body = response.json()
    except ValueError:
        return response.status_code, None
    return response.status_code, cast(dict[str, Any], body) if isinstance(body, dict) else None


def wait_until_serving(
    cfg: CloudRunConfig,
    url: str,
    tag: str,
    *,
    http_get: JsonGet = _http_get,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Poll the public URL until ``/health`` reports ``tag``'s version and ``/ready`` is 200.

    ``gcloud run services replace`` returns once the new revision has all the traffic, so this
    is the check from outside: the service is public, answers through Google's front end, and
    is the release that was tagged. The first request may also start the instance (min 0).

    Returns:
        The ``/ready`` body.

    Raises:
        DeployError: The timeout passed.
    """
    version = version_of(tag)
    base = url.rstrip("/")
    deadline = clock() + cfg.wait.timeout_seconds
    while True:
        health_status, health = http_get(f"{base}/health")
        ready_status, ready = http_get(f"{base}/ready")
        served = health.get("version") if health else None
        if health_status == 200 and served == version and ready_status == 200:
            log.info("cloud_run_ready", url=base, tag=tag, ready=ready)
            return ready or {}
        last = f"/health {health_status} (version {served}), /ready {ready_status}"
        if clock() >= deadline:
            raise DeployError(
                f"{base} not serving {tag} after {cfg.wait.timeout_seconds:.0f} s: {last}"
            )
        log.info("cloud_run_waiting", url=base, state=last)
        sleep(cfg.wait.interval_seconds)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m api.deploy.cloudrun", description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("outputs", help="the config's values as GitHub step outputs")
    render = commands.add_parser("render", help="write the service manifest for a release")
    render.add_argument("--tag", required=True)
    render.add_argument("--digest", required=True)
    render.add_argument("--out", type=Path, required=True)
    wait = commands.add_parser("wait", help="poll until the URL serves the tag and is ready")
    wait.add_argument("--tag", required=True)
    wait.add_argument("--url", required=True)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.command == "outputs":
        sys.stdout.write(github_outputs(cfg))
        return
    configure_logging()
    if args.command == "render":
        # JSON is YAML, which is what `gcloud run services replace` reads.
        args.out.write_text(json.dumps(service_manifest(cfg, args.tag, args.digest), indent=1))
        log.info("cloud_run_manifest", tag=args.tag, digest=args.digest, out=str(args.out))
    else:
        wait_until_serving(cfg, args.url, args.tag)


if __name__ == "__main__":
    main()
