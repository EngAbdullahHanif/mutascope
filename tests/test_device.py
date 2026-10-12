"""Device selection: MPS when available, else CPU; never CUDA."""

import os

import pytest
import torch

from mutascope.utils.device import select_device


def test_cpu_is_always_available() -> None:
    assert select_device("cpu") == torch.device("cpu")


def test_auto_prefers_mps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
    assert select_device("auto") == torch.device("mps")
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
    assert select_device("auto") == torch.device("cpu")


def test_explicit_mps_fails_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="not available"):
        select_device("mps")


def test_cuda_is_not_an_option() -> None:
    with pytest.raises(ValueError, match="device must be one of"):
        select_device("cuda")


def test_mps_fallback_is_enabled() -> None:
    assert os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"
