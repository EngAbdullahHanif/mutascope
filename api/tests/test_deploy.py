"""The Cloud Run deploy (ADR 0034): the service as code, by digest, and a strict wait."""

from typing import Any

import pytest

from api.deploy.cloudrun import (
    PORT,
    CloudRunConfig,
    DeployError,
    github_outputs,
    load_config,
    service_manifest,
    version_of,
    wait_until_serving,
)

DIGEST = "sha256:" + "ab" * 32


@pytest.fixture
def cfg() -> CloudRunConfig:
    return load_config().model_copy(
        update={
            "project_id": "mutascope-test",
            "project_number": "123456789012",
            "env": {"MUTASCOPE_API_SCORE_DEFAULT_MODEL": "esm2_35m"},
            "secrets": {"MUTASCOPE_API_METRICS_TOKEN": "mutascope-metrics-token"},
        }
    )


def _container(manifest: dict[str, Any]) -> dict[str, Any]:
    return manifest["spec"]["template"]["spec"]["containers"][0]


def test_the_committed_config_loads() -> None:
    cfg = load_config()
    assert cfg.max_instances == 1  # one process (ADR 0032)
    assert cfg.ghcr_image == cfg.ghcr_image.lower()


def test_an_unset_project_is_refused_with_the_fix() -> None:
    cfg = load_config().model_copy(update={"project_id": "", "project_number": ""})
    with pytest.raises(DeployError, match=r"setup-gcp\.sh"):
        service_manifest(cfg, "v0.2.0", DIGEST)


def test_image_runs_by_digest_from_artifact_registry(cfg: CloudRunConfig) -> None:
    container = _container(service_manifest(cfg, "v0.2.0", DIGEST))
    assert container["image"] == (
        f"europe-west1-docker.pkg.dev/mutascope-test/mutascope/mutascope-api@{DIGEST}"
    )
    assert container["ports"] == [{"name": "http1", "containerPort": PORT}]


def test_one_instance_two_cpus_and_ready_gates_traffic(cfg: CloudRunConfig) -> None:
    manifest = service_manifest(cfg, "v0.2.0", DIGEST)
    template = manifest["spec"]["template"]
    assert template["metadata"]["annotations"]["autoscaling.knative.dev/maxScale"] == "1"
    assert template["metadata"]["labels"] == {"mutascope-release": "v0-2-0"}
    container = _container(manifest)
    assert container["resources"]["limits"] == {"cpu": "2", "memory": "4Gi"}
    assert container["startupProbe"]["httpGet"] == {"path": "/ready", "port": PORT}
    assert template["spec"]["serviceAccountName"] == (
        "mutascope-api-runtime@mutascope-test.iam.gserviceaccount.com"
    )


def test_environment_is_exactly_the_config(cfg: CloudRunConfig) -> None:
    env = _container(service_manifest(cfg, "v0.2.0", DIGEST))["env"]
    assert env == [
        {"name": "MUTASCOPE_API_SCORE_DEFAULT_MODEL", "value": "esm2_35m"},
        {
            "name": "MUTASCOPE_API_METRICS_TOKEN",
            "valueFrom": {"secretKeyRef": {"name": "mutascope-metrics-token", "key": "latest"}},
        },
    ]


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v1.2.3-rc1", "latest"])
def test_only_release_tags_are_deployed(tag: str) -> None:
    with pytest.raises(DeployError, match="release tag"):
        version_of(tag)


def test_a_digest_is_required(cfg: CloudRunConfig) -> None:
    with pytest.raises(DeployError, match="digest"):
        service_manifest(cfg, "v1.0.0", "latest")


def test_workflow_outputs_come_from_the_config(cfg: CloudRunConfig) -> None:
    lines = dict(line.split("=", 1) for line in github_outputs(cfg).splitlines())
    assert lines["image"] == cfg.image
    assert lines["registry"] == "europe-west1-docker.pkg.dev"
    assert lines["provider"] == (
        "projects/123456789012/locations/global/workloadIdentityPools/github/providers/github"
    )
    assert lines["deployer"] == "mutascope-deployer@mutascope-test.iam.gserviceaccount.com"


def test_wait_passes_only_on_the_new_version_and_ready(cfg: CloudRunConfig) -> None:
    answers = iter(
        [
            (0, None), (0, None),  # cold start: no answer yet
            (200, {"version": "0.1.0"}), (200, {"status": "ready"}),  # old release
            (200, {"version": "0.2.0"}), (503, None),  # not ready yet
            (200, {"version": "0.2.0"}), (200, {"status": "ready"}),
        ]
    )  # fmt: skip
    ready = wait_until_serving(
        cfg,
        "https://x.run.app/",
        "v0.2.0",
        http_get=lambda url: next(answers),
        sleep=lambda s: None,
    )
    assert ready == {"status": "ready"}


def test_wait_fails_after_the_timeout(cfg: CloudRunConfig) -> None:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(DeployError, match=r"not serving v0\.2\.0"):
        wait_until_serving(
            cfg,
            "https://x.run.app",
            "v0.2.0",
            http_get=lambda url: (503, None),
            clock=lambda: now[0],
            sleep=sleep,
        )
    assert now[0] >= cfg.wait.timeout_seconds
