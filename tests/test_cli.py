"""CLI conventions the Nextflow pipeline relies on."""

import inspect
from pathlib import Path

import pytest
from hydra.errors import ConfigCompositionException
from typer.testing import CliRunner

from mutascope.cli import app, data_app, eval_app, score_app, train_app

CONF = Path(__file__).parents[1] / "conf"


@pytest.mark.parametrize(
    "command",
    [
        *data_app.registered_commands,
        *eval_app.registered_commands,
        *score_app.registered_commands,
        *train_app.registered_commands,
    ],
    ids=lambda c: c.name or "",
)
def test_every_pipeline_command_takes_hydra_overrides(command: object) -> None:
    """The pipeline appends ``params.hydra_overrides`` to every call; Typer rejects extra
    arguments, so a command without these parameters would break the ``test`` profile."""
    callback = getattr(command, "callback", None)
    assert callback is not None
    params = inspect.signature(callback).parameters
    assert {"config_dir", "overrides"} <= params.keys()


def test_unknown_override_is_rejected_by_commands_that_ignore_config(tmp_path: Path) -> None:
    """check-variants reads nothing from the config, but a typo in an override still fails."""
    (tmp_path / "dms").mkdir()
    reference = tmp_path / "ref.csv"
    reference.write_text("DMS_id,target_seq\n")
    args = ["data", "check-variants", "--config-dir", str(CONF), "--reference", str(reference)]
    args += ["--dms-dir", str(tmp_path / "dms")]

    ok = CliRunner().invoke(app, [*args, "data.structure_exceptions=null"])
    assert ok.exit_code == 0, ok.output

    typo = CliRunner().invoke(app, [*args, "data.structure_exceptons=null"])
    assert typo.exit_code == 1
    assert isinstance(typo.exception, ConfigCompositionException)
