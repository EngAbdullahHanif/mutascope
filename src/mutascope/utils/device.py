"""Pick the torch device: Apple MPS when available, else CPU.

CUDA is deliberately not considered: only the optional LoRA fine-tuning (Kaggle T4) may use it
(CLAUDE.md, "Environment facts"), and that code selects its own device.
"""

import os

# Ops MPS lacks fall back to CPU instead of raising. The Makefile exports this too; setting it
# here covers notebooks and direct CLI calls. It must be set before torch initialises MPS.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch

from mutascope.utils.logging import get_logger

log = get_logger(__name__)

DEVICES = ("auto", "mps", "cpu")


def select_device(preference: str = "auto") -> torch.device:
    """Return the device to run models on.

    Args:
        preference: ``"auto"`` (MPS if available, else CPU), ``"mps"`` (fail if unavailable)
            or ``"cpu"``.

    Raises:
        ValueError: Unknown preference.
        RuntimeError: ``"mps"`` requested but not available.
    """
    if preference not in DEVICES:
        raise ValueError(f"device must be one of {DEVICES}, got {preference!r}")
    mps = torch.backends.mps.is_available()
    if preference == "mps" and not mps:
        raise RuntimeError("device 'mps' requested but torch.backends.mps is not available")
    device = torch.device("mps" if preference != "cpu" and mps else "cpu")
    log.info("device_selected", device=str(device), preference=preference)
    return device
