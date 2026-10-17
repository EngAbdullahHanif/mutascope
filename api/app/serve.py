"""The start command: ``python -m api.app.serve`` (one uvicorn process, one worker).

This is the only supported way to run the API in production (the Docker image's command). It
enforces the single-process rule of ADR 0032 at the place a deployment would break it:

- uvicorn gets the app object, not an import string, and ``workers=1``; uvicorn cannot fork
  workers from an object, so no flag can add a second one;
- ``WEB_CONCURRENCY`` (the variable hosts use to ask for more workers) above 1 is refused;
- uvicorn's proxy-header rewriting is off: the client address is decided by
  :mod:`api.app.clients`, which believes ``X-Forwarded-For`` only from ``trusted_proxies``
  (uvicorn would believe it from 127.0.0.1);
- uvicorn's access log is off: the request-context middleware writes one JSON line per
  request, with its id; uvicorn's own log records go through the same JSON formatter.

And the app's lifespan takes a host-wide lock (:mod:`api.app.process`), for launches that do
not use this command.
"""

import os

import uvicorn

from api.app.logs import configure_api_logging
from api.app.main import create_app
from api.app.settings import Settings


class ConfigurationError(SystemExit):
    """The environment asks for more than one worker process."""


def uvicorn_config(settings: Settings) -> uvicorn.Config:
    """The server configuration of the start command."""
    workers = os.environ.get("WEB_CONCURRENCY", "1").strip() or "1"
    if workers != "1":
        raise ConfigurationError(
            f"WEB_CONCURRENCY={workers}: the API runs as one process (in-memory job queue and "
            "rate limits, ADR 0032); unset it or set it to 1"
        )
    return uvicorn.Config(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        workers=1,
        proxy_headers=False,
        access_log=False,
        log_config=None,
        server_header=False,
        limit_concurrency=settings.max_connections,
    )


def main() -> None:
    configure_api_logging()
    uvicorn.Server(uvicorn_config(Settings())).run()


if __name__ == "__main__":
    main()
