"""The committed data config parses into a well-formed Release."""

from pathlib import Path

import pytest
from omegaconf import OmegaConf

from mutascope.data.manifest import Manifest, ManifestEntry
from mutascope.data.release import ReleaseConfigError, load_release
from mutascope.utils.config import load_config

CONF = Path(__file__).parents[2] / "conf"


def test_committed_config_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MUTASCOPE_DATA_DIR", raising=False)
    release = load_release(load_config(CONF).data)

    assert release.version == "v1.3"
    assert release.raw_dir == Path("data/raw/proteingym_v1.3")
    assert release.manifest_path == Path("data/manifest.yaml")
    keys = {f.key for f in release.files}
    assert {"reference", "dms_substitutions", "cv_folds_singles", "af2_structures"} <= keys
    assert {"zero_shot_scores", "zero_shot_summary_spearman"} <= keys
    assert {"supervised_summary_spearman", "supervised_dms_level_spearman_modulo"} <= keys
    marks = [f for f in release.files if f.url.startswith("https://marks.hms.harvard.edu/")]
    assert marks and all(f.md5 is not None for f in marks)  # every upstream file is verifiable
    # Each local file is the upstream file under its own name. The supervised leaderboard's
    # files take a "Supervised_" prefix: its summary has the zero-shot summary's name.
    for f in release.files:
        upstream = f.url.rsplit("/", 1)[1]
        expected = f"Supervised_{upstream}" if f.key.startswith("supervised_") else upstream
        assert f.filename == expected, f.key
    assert len({f.filename for f in release.files}) == len(release.files)

    assert len(release.structure_exceptions) == 4
    assert all(reason for reason in release.structure_exceptions.values())

    zero_shot = release.file("zero_shot_scores").to_parquet
    assert zero_shot is not None and zero_shot.delete_archive
    assert ("ESM2_650M", "DOUBLE") in zero_shot.columns


def test_overrides_and_env_move_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUTASCOPE_DATA_DIR", "/scratch")
    assert load_release(load_config(CONF).data).raw_dir == Path("/scratch/raw/proteingym_v1.3")
    cfg = load_config(CONF, ["data.raw_dir=/elsewhere"])
    assert load_release(cfg.data).raw_dir == Path("/elsewhere")


def test_to_parquet_requires_archive() -> None:
    cfg = OmegaConf.create(
        {
            "release": {"version": "v0"},
            "raw_dir": "raw",
            "manifest_path": "m.yaml",
            "files": {
                "x": {
                    "filename": "x.csv",
                    "url": "https://e.org/x.csv",
                    "size_bytes": 1,
                    "md5": None,
                    "archive": False,
                    "to_parquet": {"columns": {"a": "DOUBLE"}, "delete_archive": True},
                }
            },
        }
    )
    with pytest.raises(ReleaseConfigError, match="requires archive"):
        load_release(cfg)


def test_manifest_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    assert Manifest.load(path).get("s", "f") is None
    manifest = Manifest.load(path)
    entry = ManifestEntry(url="https://e.org/f", size_bytes=3, md5="a" * 32, sha256="b" * 64)
    manifest.record("s", "f", entry)
    manifest.save()
    assert Manifest.load(path).get("s", "f") == entry
