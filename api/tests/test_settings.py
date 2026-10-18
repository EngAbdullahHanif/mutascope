"""The API's pins are the config's pins, and only fp32 ONNX models are served."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from api.app.scorers import ScorerLoadError, model_config
from api.app.settings import Settings
from mutascope.utils.config import load_config

CONF = Path(__file__).parents[2] / "conf"
ENV_EXAMPLE = Path(__file__).parents[2] / ".env.example"


def test_defaults_are_the_config_pins() -> None:
    cfg = load_config(CONF)
    settings = Settings()
    assert settings.bundle_repo == cfg.publish.repo_id
    assert settings.bundle_path == cfg.export.publish.path_in_repo
    assert settings.bundle_revision == cfg.export.publish.revision
    assert settings.onnx_revision == cfg.onnx.publish.revision
    assert set(settings.onnx_models) <= set(cfg.onnx.scorers)
    assert settings.onnx_threads == 2
    assert settings.config_dir == CONF


def test_served_models_are_the_fp32_configs() -> None:
    settings = Settings()
    for name in settings.onnx_models:
        cfg = model_config(CONF, name, revision=settings.onnx_revision, threads=2, local_dir=None)
        assert (cfg["precision"], cfg["revision"], cfg["threads"]) == (
            "fp32",
            settings.onnx_revision,
            2,
        )


def test_int8_is_refused() -> None:
    with pytest.raises(ScorerLoadError, match="not an fp32 ONNX scorer"):
        model_config(CONF, "esm2_35m_int8", revision="0" * 40, threads=2, local_dir=None)
    with pytest.raises(ScorerLoadError):
        model_config(CONF, "esm2_35m", revision="0" * 40, threads=2, local_dir=None)


def test_environment_overrides_and_revision_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUTASCOPE_API_ONNX_THREADS", "4")
    assert Settings().onnx_threads == 4
    monkeypatch.setenv("MUTASCOPE_API_BUNDLE_REVISION", "main")
    with pytest.raises(ValidationError):
        Settings()


def test_env_example_lists_every_setting_and_sets_none() -> None:
    """``.env.example`` documents each variable once and, copied to ``.env``, changes nothing."""
    text = ENV_EXAMPLE.read_text()
    listed = re.findall(r"^# (MUTASCOPE_API_[A-Z0-9_]+)=", text, flags=re.MULTILINE)
    expected = {f"MUTASCOPE_API_{name.upper()}" for name in Settings.model_fields}
    assert sorted(listed) == sorted(expected)
    assert not re.findall(r"^MUTASCOPE_API_", text, flags=re.MULTILINE)
