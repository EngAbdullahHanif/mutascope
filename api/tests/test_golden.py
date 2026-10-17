"""Golden tests: landscape values served by the API against the published bundle files.

The expected values are decoded here straight from the gzip JSON, independently of the API's
models (``value = q / 10**decimals``), and a few cells are pinned as literals read from the
files of ``bundle-v1.0.0``, so a change in either decoding fails.
"""

import gzip
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from api.tests.conftest import SUPERVISED, ZERO_SHOT
from mutascope.alphabet import AA_ORDER

pytestmark = pytest.mark.anyio


def _raw(bundle_dir: Path, assay_id: str) -> dict[str, Any]:
    return json.loads(
        gzip.decompress((bundle_dir / "proteins" / f"{assay_id}.json.gz").read_bytes())
    )


def _decoded(grid: dict[str, Any]) -> list[float | None]:
    scale = 10 ** grid["decimals"]
    return [None if q is None else q / scale for q in grid["values"]]


async def test_supervised_landscape_matches_the_bundle(
    client: httpx.AsyncClient, bundle_dir: Path
) -> None:
    raw = _raw(bundle_dir, SUPERVISED)
    body = (await client.get(f"/v1/proteins/{SUPERVISED}/landscape")).json()
    supervised = raw["supervised"]

    assert body["kind"] == "supervised"
    assert body["model"] == "ridge_seq_struct" == raw["default"]["model"]
    assert body["label"] == "Supervised (n = 621)" == raw["default"]["label"]
    assert body["n_measured"] == 621
    assert body["levels"] == [0.8, 0.9]
    assert body["target_seq"] == raw["assay"]["target_seq"]
    assert body["aa_order"] == AA_ORDER
    for field in ("prediction", "lower_80", "upper_80", "lower_90", "upper_90"):
        assert body[field] == _decoded(supervised[field]), field
    assert body["measured"] == _decoded(raw["measured"])
    assert body["position_status"] == supervised["position_status"]
    assert body["structure_imputed"] == supervised["structure_imputed"]
    codes = supervised["calibration_scheme"]["codes"]
    assert body["calibration_scheme"] == [
        None if v is None else codes[v] for v in supervised["calibration_scheme"]["values"]
    ]
    # Out-of-fold prediction: the prediction exactly where a variant was measured.
    measured = [v is not None for v in body["measured"]]
    assert sum(measured) == 621
    assert body["oof_prediction"] == [
        p if m else None for p, m in zip(body["prediction"], measured, strict=True)
    ]
    # Pinned cell: residue 0 (G) -> A, measured.
    assert body["measured"][0] == -0.1039
    assert body["prediction"][0] == body["oof_prediction"][0] == -0.1262
    assert (body["lower_80"][0], body["upper_80"][0]) == (-0.4863, 0.2339)
    assert (body["lower_90"][0], body["upper_90"][0]) == (-0.6714, 0.419)
    # The wild type has no supervised value.
    wt = AA_ORDER.index(raw["assay"]["target_seq"][0])
    assert body["prediction"][wt] is None
    assert body["version"]["bundle"] == raw["version"]


async def test_zero_shot_landscape_matches_the_bundle(
    client: httpx.AsyncClient, bundle_dir: Path
) -> None:
    raw = _raw(bundle_dir, ZERO_SHOT)
    default = (await client.get(f"/v1/proteins/{ZERO_SHOT}/landscape")).json()
    saprot = (await client.get(f"/v1/proteins/{ZERO_SHOT}/landscape?model=saprot")).json()
    esm2 = (await client.get(f"/v1/proteins/{ZERO_SHOT}/landscape?model=esm2_650m")).json()

    assert default == saprot  # the default of this assay is SaProt
    for body, key in ((saprot, "saprot_650m_af2"), (esm2, "esm2_650m")):
        landscape = raw["zero_shot"][key]
        assert body["kind"] == "zero_shot" and body["label"] == "Zero-shot"
        assert body["model"] == key
        assert (body["hf_id"], body["revision"]) == (landscape["hf_id"], landscape["revision"])
        assert body["scores"] == _decoded(landscape["scores"])
        assert body["measured"] == _decoded(raw["measured"])
    # Pinned cells: residue 5 -> A, and the first measured variant (residue 58 -> A).
    assert (saprot["scores"][100], esm2["scores"][100]) == (-9.594, -8.06)
    assert (saprot["scores"][1160], esm2["scores"][1160]) == (-0.56, -1.062)
    assert saprot["measured"][1160] == 1.0464
    # The wild type scores 0 (log P(wt) - log P(wt)).
    assert all(
        esm2["scores"][i * 20 + AA_ORDER.index(aa)] == 0 for i, aa in enumerate(esm2["target_seq"])
    )


async def test_supervised_protein_still_serves_zero_shot(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"/v1/proteins/{SUPERVISED}/landscape?model=esm2_650m")).json()
    assert body["kind"] == "zero_shot" and body["label"] == "Zero-shot"
    assert body["msa_region"] is None
