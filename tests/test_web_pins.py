"""The website reads the bundle at the config's pin, like the API (ADR 0028, 0037)."""

import json
from pathlib import Path

from mutascope.utils.config import load_config

ROOT = Path(__file__).parents[1]


def test_web_bundle_pin_is_the_config_pin() -> None:
    cfg = load_config(ROOT / "conf")
    pin = json.loads((ROOT / "web" / "bundle.json").read_text())
    assert pin["repo"] == cfg.publish.repo_id
    assert pin["path"] == cfg.export.publish.path_in_repo
    assert pin["tag"] == cfg.export.publish.tag
    assert pin["revision"] == cfg.export.publish.revision
