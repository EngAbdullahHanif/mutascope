"""The Space deploy (ADR 0034): one FROM line, config-as-code variables, and a strict wait."""

from collections.abc import Mapping
from typing import Any

import pytest

from api.deploy.space import (
    CONFIG,
    DeployError,
    SpaceConfig,
    deploy,
    github_outputs,
    load_config,
    space_dockerfile,
    version_of,
    wait_until_serving,
)

DIGEST = "sha256:" + "ab" * 32


class FakeSpace:
    """A Space: files, variables, secrets, a head commit and a scripted runtime."""

    def __init__(self, variables: dict[str, str] | None = None) -> None:
        self.files: dict[str, str] = {}
        self.variables = dict(variables or {})
        self.secrets: dict[str, str] = {}
        self.commits: list[str] = []
        self.restarts = 0
        self.runtimes: list[tuple[str, str | None]] = []

    def get_space_variables(self, repo_id: str) -> Mapping[str, Any]:
        return dict(self.variables)

    def add_space_variable(self, repo_id: str, key: str, value: str) -> None:
        self.variables[key] = value

    def delete_space_variable(self, repo_id: str, key: str) -> None:
        del self.variables[key]

    def add_space_secret(self, repo_id: str, key: str, value: str) -> None:
        self.secrets[key] = value

    def read_file(self, repo_id: str, path: str) -> str | None:
        return self.files.get(path)

    def commit_files(self, repo_id: str, files: Mapping[str, str], message: str) -> str:
        self.files.update(files)
        self.commits.append(f"{len(self.commits):040d}")
        return self.commits[-1]

    def restart(self, repo_id: str) -> None:
        self.restarts += 1

    def head(self, repo_id: str) -> str:
        return self.commits[-1]

    def runtime(self, repo_id: str) -> tuple[str, str | None]:
        return self.runtimes.pop(0) if len(self.runtimes) > 1 else self.runtimes[0]

    def logs(self, repo_id: str, *, build: bool) -> list[str]:
        return ["build failed" if build else "crashed"]


@pytest.fixture
def cfg() -> SpaceConfig:
    return load_config(CONFIG).model_copy(
        update={"variables": {"MUTASCOPE_API_SCORE_DEFAULT_MODEL": "esm2_35m"}}
    )


def test_config_loads_and_image_is_lowercase() -> None:
    cfg = load_config()
    assert cfg.image == cfg.image.lower()
    assert cfg.url.startswith("https://") and cfg.url.endswith(".hf.space")


def test_dockerfile_is_one_from_line_by_tag_and_digest() -> None:
    text = space_dockerfile("ghcr.io/x/mutascope-api", "v1.2.3", DIGEST)
    assert text == f"FROM ghcr.io/x/mutascope-api:v1.2.3@{DIGEST}\n"
    assert text.count("\n") == 1


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v1.2.3-rc1", "latest"])
def test_only_release_tags_are_deployed(tag: str) -> None:
    with pytest.raises(DeployError, match="release tag"):
        version_of(tag)


def test_a_digest_is_required() -> None:
    with pytest.raises(DeployError, match="digest"):
        space_dockerfile("ghcr.io/x/y", "v1.0.0", "latest")


def test_deploy_makes_the_variables_exactly_the_config(cfg: SpaceConfig) -> None:
    space = FakeSpace({"MUTASCOPE_API_SCORE_DEFAULT_MODEL": "esm2_150m", "STALE": "1"})
    deploy(space, cfg, "v0.2.0", DIGEST, {"SPACE_METRICS_TOKEN": "t" * 32})
    assert space.variables == {"MUTASCOPE_API_SCORE_DEFAULT_MODEL": "esm2_35m"}
    assert space.secrets == {"MUTASCOPE_API_METRICS_TOKEN": "t" * 32}
    assert space.files["Dockerfile"] == f"FROM {cfg.image}:v0.2.0@{DIGEST}\n"
    assert "sdk: docker" in space.files["README.md"]
    assert "app_port: 7860" in space.files["README.md"]
    assert len(space.commits) == 1


def test_a_missing_secret_is_left_alone(cfg: SpaceConfig) -> None:
    space = FakeSpace()
    deploy(space, cfg, "v0.2.0", DIGEST, {})
    assert space.secrets == {}


def test_redeploying_the_same_release_restarts_instead_of_committing(cfg: SpaceConfig) -> None:
    space = FakeSpace()
    deploy(space, cfg, "v0.2.0", DIGEST, {})
    deploy(space, cfg, "v0.2.0", DIGEST, {})
    assert len(space.commits) == 1
    assert space.restarts == 1


def _deployed(cfg: SpaceConfig) -> FakeSpace:
    space = FakeSpace()
    deploy(space, cfg, "v0.2.0", DIGEST, {})
    return space


def test_wait_ignores_the_old_container_until_the_new_commit_is_ready(cfg: SpaceConfig) -> None:
    space = _deployed(cfg)
    head = space.commits[-1]
    runtimes: list[tuple[str, str | None]] = [("RUNNING", "old"), ("BUILDING", head)]
    space.runtimes = runtimes + [("RUNNING", head)] * 3
    answers = iter(
        [
            (200, {"version": "0.1.0"}), (200, {"status": "ready"}),  # stale version
            (200, {"version": "0.2.0"}), (503, None),  # not ready yet
            (200, {"version": "0.2.0"}), (200, {"status": "ready"}),
        ]
    )  # fmt: skip
    ready = wait_until_serving(
        space, cfg, "v0.2.0", http_get=lambda url: next(answers), sleep=lambda s: None
    )
    assert ready == {"status": "ready"}


@pytest.mark.parametrize("stage", ["BUILD_ERROR", "RUNTIME_ERROR"])
def test_wait_fails_at_once_on_a_failed_space(cfg: SpaceConfig, stage: str) -> None:
    space = _deployed(cfg)
    space.runtimes = [(stage, space.commits[-1])]
    with pytest.raises(DeployError, match=stage):
        wait_until_serving(
            space, cfg, "v0.2.0", http_get=lambda url: (0, None), sleep=lambda s: None
        )


def test_wait_fails_after_the_timeout(cfg: SpaceConfig) -> None:
    space = _deployed(cfg)
    space.runtimes = [("RUNNING", space.commits[-1])]
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(DeployError, match=r"not serving v0\.2\.0"):
        wait_until_serving(
            space,
            cfg,
            "v0.2.0",
            http_get=lambda url: (503, None),
            clock=lambda: now[0],
            sleep=sleep,
        )
    assert now[0] >= cfg.wait.timeout_seconds


def test_workflow_outputs_come_from_the_config() -> None:
    cfg = load_config()
    lines = dict(line.split("=", 1) for line in github_outputs(cfg).splitlines())
    assert lines == {"image": cfg.image, "url": cfg.url, "repo_id": cfg.repo_id}
