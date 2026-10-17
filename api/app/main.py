"""FastAPI application: the bundle and the ONNX scorers are loaded once, in ``lifespan``.

Startup reads the website bundle at the pinned revision (ADR 0028) and refuses to start if its
checksums fail or its schema version is not one this code reads; then it loads the fp32 ONNX
scorers (ADR 0029). Nothing in the API imports torch or transformers.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware

from api.app import problems
from api.app.bundle import open_bundle
from api.app.caching import ETagMiddleware
from api.app.routes import AppState, router
from api.app.scorers import load_scorers
from api.app.settings import Settings
from mutascope import __version__
from mutascope.utils.logging import configure_logging, get_logger

CACHED_PREFIXES = ("/v1/proteins", "/v1/benchmarks")
"""Read-only routes that get an ETag and Cache-Control."""


def load_state(settings: Settings) -> AppState:
    """Open the pinned bundle and build the live scorers (blocking; startup only)."""
    bundle = open_bundle(
        repo=settings.bundle_repo,
        path_in_repo=settings.bundle_path,
        revision=settings.bundle_revision,
        local_dir=settings.bundle_dir,
        cache_size=settings.protein_cache_size,
    )
    scorers = load_scorers(
        settings.config_dir,
        settings.onnx_models,
        revision=settings.onnx_revision,
        threads=settings.onnx_threads,
        local_dir=settings.onnx_local_dir,
    )
    return AppState(bundle=bundle, scorers=scorers)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Load the bundle and models once; any failure stops the server from starting."""
    configure_logging(json=True)
    log = get_logger("api")
    settings: Settings = app.state.settings
    log.info("startup", version=__version__)
    app.state.mutascope = load_state(settings)
    log.info("ready", proteins=len(app.state.mutascope.bundle.entries))
    yield
    app.state.mutascope = None


def _openapi(app: FastAPI) -> dict[str, Any]:
    """The generated schema plus the problem-details component the error responses reference."""
    if app.openapi_schema is None:
        schema = FastAPI.openapi(app)
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        problem = problems.Problem.model_json_schema(ref_template="#/components/schemas/{model}")
        components.update(problem.pop("$defs", {}))
        components["Problem"] = problem
        app.openapi_schema = schema
    return app.openapi_schema


def create_app(settings: Settings | None = None) -> FastAPI:
    """The API with ``settings`` (default: from the environment)."""
    app = FastAPI(
        title="MutaScope API",
        version=__version__,
        description=(
            "Functional-effect landscapes of single amino-acid substitutions in ProteinGym "
            "DMS assays. Zero-shot and supervised predictions are labelled as such; neither "
            "is a clinical prediction."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings if settings is not None else Settings()
    app.state.mutascope = None
    problems.install(app)
    app.include_router(router)
    # Starlette runs the last-added middleware first: gzip wraps the ETag middleware, so the
    # tag is computed on the uncompressed body.
    app.add_middleware(
        ETagMiddleware, prefixes=CACHED_PREFIXES, cache_control=app.state.settings.cache_control
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.openapi = lambda: _openapi(app)  # type: ignore[method-assign]
    return app


app = create_app()
