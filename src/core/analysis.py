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


def find_3db_bandwidth(freq, gain_db, dc_gain_db=None):
    if dc_gain_db is None:
        dc_gain_db = gain_db[0]

    target = dc_gain_db - 3.0
    for f, g in zip(freq, gain_db):
        if g <= target:
            return f
    return None

def summarize_noise(noise_result: Dict) -> Dict[str, float]:
    return {
        "total_output_rms": float(noise_result["total_onoise_rms"]),
        "total_input_rms": float(noise_result["total_inoise_rms"]),
    }
