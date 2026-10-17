"""One error format for every error: RFC 9457 problem details (``application/problem+json``).

Errors the API means to raise are :class:`ApiError`, with a ``type`` URN naming the problem
(``urn:mutascope:problem:<slug>``, the same scheme as the bundle's schema ``$id``). Everything
else is mapped here too: HTTP errors from routing (404 for an unknown path, 405) get ``type``
``about:blank`` as RFC 9457 prescribes, validation errors 422 with the offending fields in
``errors``, and an unhandled exception a 500 that says nothing about the internals.
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from mutascope.utils.logging import get_logger

log = get_logger(__name__)

MEDIA_TYPE = "application/problem+json"
URN = "urn:mutascope:problem:"


class FieldError(BaseModel):
    """One invalid request field (422 only)."""

    loc: list[str | int]
    msg: str
    type: str


class Problem(BaseModel):
    """RFC 9457 problem details."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    type: str
    """URI naming the kind of problem; ``about:blank`` when the status says it all."""
    title: str
    """Short summary of the kind of problem; the same for every occurrence."""
    status: int
    detail: str
    """What went wrong in this request."""
    instance: str
    """The request path."""
    errors: list[FieldError] | None = None
    """Invalid fields, for 422."""


REQUEST_ID_HEADER = "X-Request-ID"


class ApiError(Exception):
    """An error the API reports on purpose."""

    def __init__(
        self,
        status: int,
        slug: str,
        title: str,
        detail: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status, self.type, self.title, self.detail = status, URN + slug, title, detail
        self.headers = dict(headers or {})


class BodyTooLarge(StarletteHTTPException):
    """A streamed request body passed the limit (raised while the body is read).

    An HTTP exception, not an :class:`ApiError`: FastAPI re-raises HTTP exceptions met while
    reading a body (any other exception becomes a 400), so the handler below formats it.
    """

    def __init__(self, limit: int) -> None:
        super().__init__(413, detail=body_too_large_detail(limit))


def body_too_large_detail(limit: int) -> str:
    return f"The request body is larger than {limit} bytes."


BODY_TOO_LARGE = URN + "body-too-large"


def protein_not_found(assay_id: str) -> ApiError:
    return ApiError(
        404,
        "protein-not-found",
        "Protein not found",
        f"No protein {assay_id!r} in the bundle. Protein ids are ProteinGym assay ids; "
        "GET /v1/proteins lists them.",
    )


def model_not_available(assay_id: str, model: str, why: str) -> ApiError:
    return ApiError(
        404,
        "model-not-available",
        "Model not available for this protein",
        f"{model} has no landscape for {assay_id}: {why}",
    )


def job_not_found(job_id: str) -> ApiError:
    return ApiError(
        404,
        "job-not-found",
        "Job not found",
        f"No job {job_id!r}. Finished jobs are deleted one hour after they finish.",
    )


def job_finished(job_id: str, status: str) -> ApiError:
    return ApiError(
        409,
        "job-finished",
        "Job already finished",
        f"Job {job_id} has {status}; nothing to cancel.",
    )


def queue_full(size: int) -> ApiError:
    return ApiError(
        429,
        "queue-full",
        "Job queue full",
        f"{size} precise-mode jobs are already waiting behind the running one. Try again later.",
    )


def too_long_for_precise(length: int, limit: int) -> ApiError:
    return ApiError(
        422,
        "sequence-too-long",
        "Sequence too long for precise mode",
        f"Precise mode accepts at most {limit} residues (about 3 minutes on the server); "
        f"this sequence has {length}. Fast mode (POST /v1/score) accepts up to 1022.",
    )


def rate_limited(what: str, limit: str, retry_after: int) -> ApiError:
    return ApiError(
        429,
        "rate-limited",
        "Too many requests",
        f"Rate limit for {what} exceeded ({limit}). Retry after {retry_after} s.",
        headers={"Retry-After": str(retry_after)},
    )


def too_many_streams(limit: int, scope: str) -> ApiError:
    return ApiError(
        429,
        "too-many-streams",
        "Too many open streams",
        f"{limit} event streams are already open {scope}. Close one, or poll GET /v1/jobs/{{id}}.",
    )


def too_many_jobs(limit: int) -> ApiError:
    return ApiError(
        429,
        "too-many-jobs",
        "Too many active jobs",
        f"You already have {limit} queued or running precise-mode job(s). Wait for it to "
        "finish, or cancel it (DELETE /v1/jobs/{id}).",
    )


def unauthorized() -> ApiError:
    return ApiError(
        401,
        "unauthorized",
        "Unauthorized",
        "This route needs a bearer token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def not_ready(detail: str) -> ApiError:
    return ApiError(503, "not-ready", "Not ready", detail)


def problem_response(request: Request, problem: Problem) -> JSONResponse:
    content = problem.model_dump(mode="json", exclude_none=True)
    return JSONResponse(content, status_code=problem.status, media_type=MEDIA_TYPE)


def _problem(
    request: Request, status: int, detail: str, type_: str = "about:blank", title: str = ""
) -> Problem:
    return Problem(
        type=type_,
        title=title or HTTPStatus(status).phrase,
        status=status,
        detail=detail,
        instance=request.url.path,
    )


async def _api_problem(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    response = problem_response(
        request, _problem(request, exc.status, exc.detail, exc.type, exc.title)
    )
    response.headers.update(exc.headers)
    return response


def allowed_methods(request: Request) -> list[str]:
    """Every method some route serves at this path.

    Starlette's 405 names only the methods of the first route that matched the path, but one
    path can have several routes (``GET`` and ``DELETE /v1/jobs/{id}``), and RFC 9110 asks
    ``Allow`` to list them all.
    """
    methods: set[str] = set()
    scope = {**request.scope, "method": "GET"}
    for route in request.app.router.routes:
        match, _ = route.matches(scope)
        if match is not Match.NONE:
            methods |= set(getattr(route, "methods", None) or ())
    return sorted(methods)


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    if isinstance(exc, BodyTooLarge):
        problem = _problem(request, 413, exc.detail, BODY_TOO_LARGE, "Request body too large")
    else:
        problem = _problem(request, exc.status_code, exc.detail)
    response = problem_response(request, problem)
    if exc.headers:
        response.headers.update(exc.headers)
    if exc.status_code == 405:
        response.headers["Allow"] = ", ".join(allowed_methods(request))
    return response


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    raw: list[dict[str, Any]] = list(exc.errors())
    errors = [
        FieldError(loc=list(e.get("loc", ())), msg=str(e.get("msg", "")), type=str(e["type"]))
        for e in raw
    ]
    fields = ", ".join(".".join(str(part) for part in e.loc) for e in errors)
    problem = _problem(
        request, 422, f"Invalid request parameters: {fields}.", URN + "invalid-request"
    )
    return problem_response(request, problem.model_copy(update={"errors": errors}))


def body_too_large(request: Request, limit: int) -> JSONResponse:
    """The 413 response, for middleware that rejects a body before any route runs."""
    problem = _problem(
        request, 413, body_too_large_detail(limit), BODY_TOO_LARGE, "Request body too large"
    )
    return problem_response(request, problem)


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    """A 500 that says nothing about the internals.

    Starlette runs this handler outside every middleware, after the request-context middleware
    has reset its log context, so the request id is taken from the request state here, for the
    log line and the response header. (Sentry's Starlette integration reports the exception.)
    """
    request_id = getattr(request.state, "request_id", None)
    log.error("unhandled_error", path=request.url.path, error=repr(exc), request_id=request_id)
    response = problem_response(request, _problem(request, 500, "An internal error occurred."))
    if request_id is not None:
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


def install(app: FastAPI) -> None:
    """Register the problem handlers on ``app``."""
    app.add_exception_handler(ApiError, _api_problem)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)


def responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` entries documenting problem bodies for ``statuses``."""
    schema = {"$ref": "#/components/schemas/Problem"}
    return {
        status: {
            "description": HTTPStatus(status).phrase,
            "content": {MEDIA_TYPE: {"schema": schema}},
        }
        for status in statuses
    }
