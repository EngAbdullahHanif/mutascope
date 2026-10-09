"""Smoke tests: the package installs, imports and exposes a working CLI."""

import importlib
from importlib.metadata import version

import pytest
from typer.testing import CliRunner

import mutascope
from mutascope.cli import app

SUBPACKAGES = ["data", "scoring", "features", "models", "eval", "export", "utils"]


def test_version_matches_installed_metadata() -> None:
    assert mutascope.__version__ == version("mutascope")


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    importlib.import_module(f"mutascope.{name}")


def test_cli_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == mutascope.__version__
