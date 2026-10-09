"""Write the OpenAPI schema to a file without starting a server (used by ``make types``).

Usage: ``python -m api.app.openapi <output.json>``
"""

import json
import sys
from pathlib import Path

from api.app.main import app


def write_openapi(path: Path) -> None:
    """Serialise the app's OpenAPI schema to ``path``."""
    path.write_text(json.dumps(app.openapi(), indent=2) + "\n")


if __name__ == "__main__":
    write_openapi(Path(sys.argv[1]))
