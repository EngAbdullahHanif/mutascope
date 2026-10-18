"""Deploy a released API image to the Hugging Face Space, then wait until it serves (ADR 0034).

Usage (CI's release workflow; ``HF_TOKEN`` in the environment)::

    python -m api.deploy.space deploy --tag v0.2.0 --digest sha256:<64 hex>
    python -m api.deploy.space wait --tag v0.2.0
    python -m api.deploy.space outputs >> "$GITHUB_OUTPUT"   # image, url, repo_id

The image is built and pushed to GHCR once, by CI, from a version tag. The Space never builds
the API itself: its Dockerfile is the single line ``FROM <image>:<tag>@<digest>``, so the Space
runs exactly the bytes CI built, tested and pushed, and the tag in the line says which release
that is. ``deploy`` first makes the Space's variables those of ``conf/deploy/space.yaml`` (and
sets the secrets CI holds), then commits the README and the Dockerfile in one commit.

``wait`` passes only when all three hold at once: the Space runs its latest commit (not the
previous container, which keeps answering while the new one builds), ``/health`` reports the
tag's version, and ``/ready`` answers 200 (bundle and models loaded). A build or runtime error
fails at once, with the end of the Space's logs.
"""

import argparse
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol, cast

import httpx
from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict, Field

from mutascope.utils.logging import configure_logging, get_logger

log = get_logger(__name__)

CONFIG = Path(__file__).resolve().parents[2] / "conf" / "deploy" / "space.yaml"
TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
FAILED_STAGES = frozenset({"BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "NO_APP_FILE"})
"""Stages a Space does not leave without a new commit or a restart: waiting longer is pointless."""
LOG_TAIL = 40


class DeployError(RuntimeError):
    """The deploy could not be made, or the Space did not come up serving the release."""


class WaitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    timeout_seconds: float = Field(gt=0)
    interval_seconds: float = Field(gt=0)


class SpaceConfig(BaseModel):
    """``conf/deploy/space.yaml``."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    repo_id: str
    url: str
    image: str = Field(pattern=r"^[a-z0-9./_-]+$")  # registries want lowercase names
    title: str
    short_description: str = Field(max_length=60)  # the Hub's limit
    variables: dict[str, str]
    secrets: dict[str, str]
    wait: WaitConfig


class SpaceApi(Protocol):
    """The part of ``huggingface_hub.HfApi`` the deploy uses (a fake in the tests)."""

    def get_space_variables(self, repo_id: str) -> Mapping[str, Any]: ...
    def add_space_variable(self, repo_id: str, key: str, value: str) -> Any: ...
    def delete_space_variable(self, repo_id: str, key: str) -> Any: ...
    def add_space_secret(self, repo_id: str, key: str, value: str) -> Any: ...
    def read_file(self, repo_id: str, path: str) -> str | None: ...
    def commit_files(self, repo_id: str, files: Mapping[str, str], message: str) -> str: ...
    def restart(self, repo_id: str) -> None: ...
    def head(self, repo_id: str) -> str: ...
    def runtime(self, repo_id: str) -> tuple[str, str | None]: ...
    def logs(self, repo_id: str, *, build: bool) -> list[str]: ...


class HubSpaceApi:
    """:class:`SpaceApi` over ``huggingface_hub`` (token from ``HF_TOKEN``)."""

    def __init__(self) -> None:
        from huggingface_hub import HfApi

        self.api = HfApi()

    def get_space_variables(self, repo_id: str) -> Mapping[str, Any]:
        return self.api.get_space_variables(repo_id)

    def add_space_variable(self, repo_id: str, key: str, value: str) -> Any:
        return self.api.add_space_variable(repo_id, key, value)

    def delete_space_variable(self, repo_id: str, key: str) -> Any:
        return self.api.delete_space_variable(repo_id, key)

    def add_space_secret(self, repo_id: str, key: str, value: str) -> Any:
        return self.api.add_space_secret(repo_id, key, value)

    def read_file(self, repo_id: str, path: str) -> str | None:
        from huggingface_hub import hf_hub_download  # pyright: ignore[reportUnknownVariableType]
        from huggingface_hub.errors import EntryNotFoundError

        try:
            local = hf_hub_download(repo_id, path, repo_type="space")
        except EntryNotFoundError:
            return None
        return Path(local).read_text()

    def commit_files(self, repo_id: str, files: Mapping[str, str], message: str) -> str:
        from huggingface_hub import CommitOperationAdd

        operations = [
            CommitOperationAdd(path_in_repo=path, path_or_fileobj=content.encode())
            for path, content in files.items()
        ]
        info = self.api.create_commit(
            repo_id, operations, commit_message=message, repo_type="space"
        )
        return str(info.oid)  # pyright: ignore[reportAttributeAccessIssue]

    def restart(self, repo_id: str) -> None:
        self.api.restart_space(repo_id)

    def head(self, repo_id: str) -> str:
        sha = self.api.space_info(repo_id).sha
        if sha is None:
            raise DeployError(f"{repo_id} has no commit")
        return sha

    def runtime(self, repo_id: str) -> tuple[str, str | None]:
        runtime = self.api.get_space_runtime(repo_id)
        raw = cast(dict[str, Any], runtime.raw)  # pyright: ignore[reportUnknownMemberType]
        sha = raw.get("sha")
        return str(runtime.stage), str(sha) if sha else None

    def logs(self, repo_id: str, *, build: bool) -> list[str]:
        return list(self.api.fetch_space_logs(repo_id, build=build))


def load_config(path: Path = CONFIG) -> SpaceConfig:
    raw = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    return SpaceConfig.model_validate(raw)


def version_of(tag: str) -> str:
    """``v0.2.0`` → ``0.2.0``: the package version the image's ``/health`` must report."""
    match = TAG.match(tag)
    if match is None:
        raise DeployError(f"not a release tag: {tag!r} (expected vMAJOR.MINOR.PATCH)")
    return match.group(1)


def space_dockerfile(image: str, tag: str, digest: str) -> str:
    """The Space's whole Dockerfile: the released image, by tag and by digest.

    The tag says which release runs; the digest makes the line immutable, so retagging the
    image in the registry cannot change what the Space pulls.
    """
    version_of(tag)
    if DIGEST.match(digest) is None:
        raise DeployError(f"not an image digest: {digest!r}")
    return f"FROM {image}:{tag}@{digest}\n"


def space_readme(cfg: SpaceConfig, tag: str) -> str:
    """The Space's README: its metadata (Docker SDK, port 7860) and where the code lives."""
    return (
        "---\n"
        f"title: {cfg.title}\n"
        "emoji: 🧬\n"
        "colorFrom: blue\n"
        "colorTo: red\n"
        "sdk: docker\n"
        "app_port: 7860\n"
        "pinned: false\n"
        "license: mit\n"
        f"short_description: {cfg.short_description}\n"
        "---\n\n"
        f"# {cfg.title} ({tag})\n\n"
        "Written by the release workflow of https://github.com/EngAbdullahHanif/mutascope;"
        " do not edit here.\n"
        "The Dockerfile pulls the image CI built and tested for this tag. Every score this API"
        " returns is zero-shot: a functional effect in DMS assays, not a clinical prediction.\n"
    )


def sync_settings(
    api: SpaceApi, cfg: SpaceConfig, environ: Mapping[str, str]
) -> dict[str, list[str]]:
    """Make the Space's variables exactly ``cfg.variables``; set the secrets CI has.

    Returns the names changed, by kind, for the log (values are never logged).
    """
    current = {
        key: getattr(var, "value", var) for key, var in api.get_space_variables(cfg.repo_id).items()
    }
    changes: dict[str, list[str]] = {"set": [], "deleted": [], "secrets": [], "secrets_skipped": []}
    for key, value in cfg.variables.items():
        if current.get(key) != value:
            api.add_space_variable(cfg.repo_id, key, value)
            changes["set"].append(key)
    for key in sorted(set(current) - set(cfg.variables)):
        api.delete_space_variable(cfg.repo_id, key)
        changes["deleted"].append(key)
    for key, env in cfg.secrets.items():
        value = environ.get(env, "")
        if value:
            api.add_space_secret(cfg.repo_id, key, value)
            changes["secrets"].append(key)
        else:
            changes["secrets_skipped"].append(key)
    return changes


def deploy(
    api: SpaceApi, cfg: SpaceConfig, tag: str, digest: str, environ: Mapping[str, str]
) -> str:
    """Settings first, then one commit of the README and the Dockerfile; returns the commit.

    The settings go first because the commit starts the build that should read them. If the
    files already say this release (a rerun), nothing is committed and the Space is restarted,
    so the settings still take effect and ``wait`` still has a fresh start to watch.
    """
    files = {
        "README.md": space_readme(cfg, tag),
        "Dockerfile": space_dockerfile(cfg.image, tag, digest),
    }
    changes = sync_settings(api, cfg, environ)
    log.info("space_settings_synced", space=cfg.repo_id, **changes)
    if all(api.read_file(cfg.repo_id, path) == content for path, content in files.items()):
        api.restart(cfg.repo_id)
        sha = api.head(cfg.repo_id)
        log.info("space_restarted", space=cfg.repo_id, tag=tag, commit=sha[:7])
        return sha
    sha = api.commit_files(cfg.repo_id, files, f"Deploy {tag}")
    log.info("space_deployed", space=cfg.repo_id, tag=tag, digest=digest, commit=sha[:7])
    return sha


JsonGet = Callable[[str], tuple[int, dict[str, Any] | None]]
"""``url -> (status, JSON object or None)``; status 0 when the request did not complete."""


def _http_get(url: str) -> tuple[int, dict[str, Any] | None]:
    try:
        response = httpx.get(url, timeout=10, follow_redirects=True)
    except httpx.HTTPError:
        return 0, None
    try:
        body = response.json()
    except ValueError:
        return response.status_code, None
    return response.status_code, cast(dict[str, Any], body) if isinstance(body, dict) else None


def wait_until_serving(
    api: SpaceApi,
    cfg: SpaceConfig,
    tag: str,
    *,
    http_get: JsonGet = _http_get,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Poll until the Space runs its latest commit, reports ``tag``'s version and is ready.

    Returns:
        The ``/ready`` body.

    Raises:
        DeployError: The Space failed to build or start, or the timeout passed.
    """
    version = version_of(tag)
    head = api.head(cfg.repo_id)
    deadline = clock() + cfg.wait.timeout_seconds
    last = "not polled"
    while True:
        stage, running_sha = api.runtime(cfg.repo_id)
        if stage in FAILED_STAGES:
            tail = api.logs(cfg.repo_id, build=stage in {"BUILD_ERROR", "CONFIG_ERROR"})
            for line in tail[-LOG_TAIL:]:
                log.error("space_log", line=line.rstrip())
            raise DeployError(f"{cfg.repo_id} is {stage} at {head[:7]}")
        if stage == "RUNNING" and running_sha == head:
            health_status, health = http_get(f"{cfg.url}/health")
            ready_status, ready = http_get(f"{cfg.url}/ready")
            served = health.get("version") if health else None
            if health_status == 200 and served == version and ready_status == 200:
                log.info("space_ready", space=cfg.repo_id, tag=tag, commit=head[:7], ready=ready)
                return ready or {}
            last = f"/health {health_status} (version {served}), /ready {ready_status}"
        else:
            last = f"stage {stage}, running {str(running_sha)[:7]}, head {head[:7]}"
        if clock() >= deadline:
            raise DeployError(
                f"{cfg.repo_id} not serving {tag} after {cfg.wait.timeout_seconds:.0f} s: {last}"
            )
        log.info("space_waiting", space=cfg.repo_id, state=last)
        sleep(cfg.wait.interval_seconds)


def github_outputs(cfg: SpaceConfig) -> str:
    """``key=value`` lines for ``$GITHUB_OUTPUT``, so workflows read the config, not a copy."""
    return f"image={cfg.image}\nurl={cfg.url}\nrepo_id={cfg.repo_id}\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m api.deploy.space", description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    push = commands.add_parser("deploy", help="sync settings, commit the Dockerfile")
    push.add_argument("--tag", required=True)
    push.add_argument("--digest", required=True)
    wait = commands.add_parser("wait", help="poll until the Space serves the tag and is ready")
    wait.add_argument("--tag", required=True)
    commands.add_parser("outputs", help="image, url and repo_id as GitHub step outputs")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.command == "outputs":
        sys.stdout.write(github_outputs(cfg))
        return
    configure_logging()
    api = HubSpaceApi()
    if args.command == "deploy":
        deploy(api, cfg, args.tag, args.digest, os.environ)
    else:
        wait_until_serving(api, cfg, args.tag)


if __name__ == "__main__":
    main()
