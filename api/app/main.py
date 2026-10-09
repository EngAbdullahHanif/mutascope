"""FastAPI application. Models will be loaded once in ``lifespan`` (Phase 4)."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from mutascope import __version__
from mutascope.utils.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Set up process-wide resources on startup and release them on shutdown."""
    configure_logging(json=True)
    get_logger("api").info("startup", version=__version__)
    yield


app = FastAPI(title="MutaScope API", version=__version__, lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe used by the Docker HEALTHCHECK."""
    return {"status": "ok", "version": __version__}
