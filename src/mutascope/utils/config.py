"""Load the Hydra config outside of a ``@hydra.main`` app (the CLI is Typer, not Hydra)."""

from pathlib import Path
from typing import Any, cast

from hydra import compose, initialize_config_dir
from omegaconf import DictConfig


def load_config(config_dir: Path, overrides: list[str] | None = None) -> DictConfig:
    """Compose ``config.yaml`` from ``config_dir`` with Hydra command-line style overrides.

    Args:
        config_dir: Directory holding ``config.yaml`` and the config groups.
        overrides: e.g. ``["data.raw_dir=/tmp/raw"]``.
    """
    with initialize_config_dir(config_dir=str(config_dir.resolve()), version_base=None):
        return compose(config_name="config", overrides=overrides or [])


def as_str_dict(value: object, what: str) -> dict[str, Any]:
    """Narrow a parsed YAML value to a string-keyed dict, or raise naming the offending key.

    Raises:
        TypeError: ``value`` is not a mapping.
    """
    if not isinstance(value, dict):
        raise TypeError(f"{what} must be a mapping, got {type(value).__name__}")
    return {str(k): v for k, v in cast("dict[object, Any]", value).items()}
