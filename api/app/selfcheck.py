"""Start the API here, use it, and check torch was never loaded (``python -m api.app.selfcheck``).

One check, run in two places (ADR 0033):

- ``api/tests/test_no_torch.py`` runs it in a fresh interpreter of the development
  environment, where torch and transformers are installed, so their absence from
  ``sys.modules`` shows the API never imports them (ADR 0029's test pattern);
- the API image runs it as its last build step, as the image's user and offline, so the image
  that ships has started from its own baked-in files, scored with both models and loaded neither
  module (it has neither installed: that the import does not fail matters as much).

The app's settings come from the environment, as in production. The lifespan runs in full
(lock, bundle checks, scorers, score cache, job worker), then ``/ready``, the default
landscape of the first protein in the manifest, and one fast-mode landscape of ubiquitin
from every served scorer.
"""

import asyncio
import sys

import httpx

from api.app.main import create_app

UBIQUITIN = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
FORBIDDEN = ("torch", "transformers")


class SelfCheckError(RuntimeError):
    """The API did not start, answer or score as expected, or loaded a forbidden module."""


async def _exercise(assay_id: str | None) -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://selfcheck") as client:
            ready = await client.get("/ready")
            if ready.status_code != 200:
                raise SelfCheckError(f"/ready: {ready.status_code} {ready.text[:500]}")
            if assay_id is None:
                assay_id = str((await client.get("/v1/proteins")).json()["proteins"][0]["assay_id"])
            landscape = await client.get(f"/v1/proteins/{assay_id}/landscape")
            if landscape.status_code != 200:
                raise SelfCheckError(f"landscape of {assay_id}: {landscape.status_code}")
        for name, scorer in app.state.mutascope.scorers.items():
            shape = scorer.landscape(UBIQUITIN, fast=True).shape
            if shape != (len(UBIQUITIN), 20):
                raise SelfCheckError(f"{name}: landscape shape {shape}")


def run(assay_id: str | None = None) -> None:
    """Exercise the app, then refuse if torch or transformers was imported.

    Args:
        assay_id: The protein whose default landscape is requested; None for the first in the
            manifest (a local test bundle may hold only some protein files).

    Raises:
        SelfCheckError: Any step failed, or a forbidden module is in ``sys.modules``.
    """
    asyncio.run(_exercise(assay_id))
    loaded = sorted(m for m in FORBIDDEN if m in sys.modules)
    if loaded:
        raise SelfCheckError(f"the API imported {loaded}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
