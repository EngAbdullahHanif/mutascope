"""The committed checksum manifest (``data/manifest.yaml``).

Layout, one section per release so several pins can coexist::

    proteingym_v1.3:
      DMS_substitutions.csv:
        url: https://...
        size_bytes: 208734
        md5: c434...
        sha256: 9f2a...
"""

from dataclasses import asdict, dataclass
from pathlib import Path

from omegaconf import OmegaConf

from mutascope.utils.config import as_str_dict


@dataclass(frozen=True)
class ManifestEntry:
    """What one verified download looked like."""

    url: str
    size_bytes: int
    md5: str
    sha256: str


class Manifest:
    """In-memory manifest; ``save`` writes it back atomically."""

    def __init__(self, path: Path, sections: dict[str, dict[str, ManifestEntry]]) -> None:
        self.path = path
        self._sections = sections

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        """Read the manifest, or start an empty one if the file does not exist yet."""
        if not path.exists():
            return cls(path, {})
        raw = as_str_dict(OmegaConf.to_container(OmegaConf.load(path)) or {}, str(path))
        sections = {
            section: {
                name: ManifestEntry(**as_str_dict(entry, f"{section}.{name}"))
                for name, entry in as_str_dict(files, section).items()
            }
            for section, files in raw.items()
        }
        return cls(path, sections)

    def get(self, section: str, filename: str) -> ManifestEntry | None:
        """Return the recorded entry for a file, if any."""
        return self._sections.get(section, {}).get(filename)

    def record(self, section: str, filename: str, entry: ManifestEntry) -> None:
        """Add or replace a file's entry (callers only replace after verifying)."""
        self._sections.setdefault(section, {})[filename] = entry

    def save(self) -> None:
        """Write sorted YAML via a temp file + rename, so a crash never leaves it half-written."""
        data = {
            section: {name: asdict(files[name]) for name in sorted(files)}
            for section, files in sorted(self._sections.items())
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        OmegaConf.save(OmegaConf.create(data), tmp)
        tmp.replace(self.path)
