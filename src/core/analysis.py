"""
Analysis helpers for extracting metrics from SPICE results.

Not really used in the current math-only MVP, but kept as a placeholder
for when we plug in real SPICE simulations.
"""

from __future__ import annotations
from typing import Dict


def extract_gain_from_spice_output(results: Dict) -> float:
    """
    Extract gain from SPICE results dict.

    For now, just pass through a dummy field.
    """
    return float(results.get("gain_db", 0.0))
