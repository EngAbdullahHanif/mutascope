"""Smoke-test the launched website and the live API (Phase 5, Task 7).

Usage (from the repository root; standard library only)::

    uv run python tools/smoke/launch.py
    uv run python tools/smoke/launch.py --site https://mutascope.abdullahhanif.me \
        --api https://mutascope-api-2lvmztrsha-ew.a.run.app --assay GFP_AEQVI_Sarkisyan_2016

Not a CI job: it checks the production deployment, which changes outside a pull request. It
logs one line per check and exits 1 if any check fails.

1. **API awake:** ``GET /health`` answers 200 within ``--cold-start`` seconds (the service
   scales to zero; a first request can take ~20 s).
2. **Pages:** ``/``, ``/explore``, ``/explore/<assay>``, ``/score``, ``/benchmarks``,
   ``/how-it-works`` and ``/about`` return 200, and the protein page's HTML contains the
   protein's name in its title (``molecule_name`` from ``GET /v1/proteins/<assay>``).
3. **CORS preflights** of ``POST /v1/score``: each ``--allowed-origin`` gets its own origin
   back in ``access-control-allow-origin``; ``--denied-origin`` gets no such header.
4. **Fast mode from the site:** ``POST /v1/score`` with ``Origin: <site>``, one substitution of
   the assay's own target sequence, returns 200, ``kind: zero_shot`` and the CORS header.

The sequence and the mutation are never logged (CLAUDE.md, API logs): only their length.
"""

import argparse
import http.client
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

SITE = "https://mutascope.abdullahhanif.me"
API = "https://mutascope-api-2lvmztrsha-ew.a.run.app"
ASSAY = "GFP_AEQVI_Sarkisyan_2016"
PAGES = ("/", "/explore", "/explore/{assay}", "/score", "/benchmarks", "/how-it-works", "/about")
DENIED_ORIGIN = "https://evil.example"
log = logging.getLogger("smoke")


@dataclass(frozen=True)
class Response:
    status: int
    """0 when the connection failed or timed out without an answer."""
    headers: dict[str, str]
    """Lower-cased names."""
    body: bytes


def request(
    url: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> Response:
    """One request; an HTTP error is a response, a network failure is status 0."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", "mutascope-smoke/1")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            headers = {k.lower(): v for k, v in response.headers.items()}
            return Response(response.status, headers, response.read())
    except urllib.error.HTTPError as error:
        return Response(error.code, {k.lower(): v for k, v in error.headers.items()}, error.read())
    except (http.client.HTTPException, OSError) as error:
        log.warning("no answer from %s: %r", url, error)
        return Response(0, {}, b"")


class Checks:
    """Collects check results; every result is logged as it is made."""

    def __init__(self) -> None:
        self.failed: list[str] = []
        self.passed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        log.info("%s  %s%s", "PASS" if ok else "FAIL", name, f"  ({detail})" if detail else "")
        if ok:
            self.passed += 1
        else:
            self.failed.append(name)
        return ok


def wait_for_health(api: str, budget_s: float) -> tuple[Response, float]:
    """``GET /health`` until 200 or ``budget_s`` runs out; returns the last answer and the wait."""
    started = time.monotonic()
    while True:
        remaining = budget_s - (time.monotonic() - started)
        response = request(f"{api}/health", timeout=max(1.0, remaining))
        elapsed = time.monotonic() - started
        if response.status == 200 or elapsed >= budget_s:
            return response, elapsed
        time.sleep(min(2.0, max(0.0, budget_s - elapsed)))


def preflight(api: str, origin: str) -> Response:
    return request(
        f"{api}/v1/score",
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )


def substitution(sequence: str) -> str:
    """One valid substitution at the first residue (1-indexed, as the API reads mutations)."""
    wt = sequence[0]
    return f"{wt}1{'A' if wt != 'A' else 'G'}"


def run(args: argparse.Namespace) -> Checks:
    site, api = args.site.rstrip("/"), args.api.rstrip("/")
    checks = Checks()

    health, waited = wait_for_health(api, args.cold_start)
    version = json.loads(health.body).get("version") if health.status == 200 else None
    checks.check(
        "API /health 200",
        health.status == 200,
        f"status {health.status}, {waited:.1f} s, version {version}",
    )

    protein = request(f"{api}/v1/proteins/{args.assay}")
    assay: dict[str, Any] = json.loads(protein.body)["assay"] if protein.status == 200 else {}
    checks.check(f"API /v1/proteins/{args.assay} 200", protein.status == 200, f"{protein.status}")

    for template in PAGES:
        path = template.format(assay=args.assay)
        page = request(f"{site}{path}")
        checks.check(f"site {path} 200", page.status == 200, f"status {page.status}")
        if "{assay}" in template:
            # The page title, not the bare name: a name like "GFP" is also in the assay id.
            name = assay.get("molecule_name")
            title = f"<title>{name} ({args.assay})"
            found = bool(name) and title in page.body.decode("utf-8", "replace")
            checks.check(f"site {path} names the protein", found, f"looked for {title!r}")

    for origin in args.allowed_origin:
        answer = preflight(api, origin)
        allowed = answer.headers.get("access-control-allow-origin")
        checks.check(
            f"CORS preflight allowed: {origin}",
            answer.status == 200 and allowed == origin,
            f"status {answer.status}, access-control-allow-origin {allowed!r}",
        )
    answer = preflight(api, args.denied_origin)
    allowed = answer.headers.get("access-control-allow-origin")
    checks.check(
        f"CORS preflight refused: {args.denied_origin}",
        answer.status != 0 and allowed is None,
        f"status {answer.status}, access-control-allow-origin {allowed!r}",
    )

    sequence = str(assay.get("target_seq", ""))
    if not sequence:
        checks.check("POST /v1/score from the site", False, "no target sequence to score")
        return checks
    scored = request(
        f"{api}/v1/score",
        method="POST",
        body={"sequence": sequence, "mutations": [substitution(sequence)]},
        headers={"Origin": site},
        timeout=args.cold_start + 30,
    )
    result: dict[str, Any] = json.loads(scored.body) if scored.status == 200 else {}
    allowed = scored.headers.get("access-control-allow-origin")
    checks.check(
        "POST /v1/score from the site",
        scored.status == 200
        and result.get("kind") == "zero_shot"
        and len(result.get("mutations") or []) == 1,
        f"status {scored.status}, {len(sequence)} residues, model {result.get('model')}, "
        f"cache {scored.headers.get('x-mutascope-cache')}",
    )
    checks.check(
        "POST /v1/score carries the CORS header",
        allowed == site,
        f"access-control-allow-origin {allowed!r}",
    )
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the website and the live API.")
    parser.add_argument("--site", default=SITE)
    parser.add_argument("--api", default=API)
    parser.add_argument("--assay", default=ASSAY)
    parser.add_argument(
        "--allowed-origin",
        action="append",
        help="an origin the API must allow (repeatable; default: --site)",
    )
    parser.add_argument("--denied-origin", default=DENIED_ORIGIN)
    parser.add_argument("--cold-start", type=float, default=30, help="seconds to wait for /health")
    args = parser.parse_args()
    args.allowed_origin = args.allowed_origin or [args.site.rstrip("/")]
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    checks = run(args)
    log.info("%d passed, %d failed", checks.passed, len(checks.failed))
    if checks.failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
