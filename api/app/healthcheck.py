"""The image's ``HEALTHCHECK``: exit 0 if ``/health`` answers 200 (``-m api.app.healthcheck``).

Standard library only, so a probe every 30 s costs an interpreter start and one request. It
reads the port the server was told to bind (``MUTASCOPE_API_PORT``), so a changed port does not
make a healthy container look unhealthy.
"""

import os
import sys
import urllib.request

DEFAULT_PORT = "7860"


def probe(port: str, timeout: float = 4.0) -> bool:
    """True when the liveness route answers 200 on this host."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as reply:
            return bool(reply.status == 200)
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(0 if probe(os.environ.get("MUTASCOPE_API_PORT", DEFAULT_PORT)) else 1)
