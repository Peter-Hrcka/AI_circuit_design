"""
Very simple goal parsing for the AI layer.

Later we might plug a real LLM here. For now, we keep it rule-based:
- "set gain to X dB"
- "increase gain to X dB"
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class GainGoal:
    target_gain_db: float


def parse_goal(text: str) -> Optional[GainGoal]:
    """
    Extremely naive parser that looks for a number followed by 'dB'.

    Examples:
    - "set gain to 40 dB"
    - "increase gain to 32.5 dB"

    Returns None if nothing is recognized.
    """
    lowered = text.lower()
    if "gain" not in lowered or "db" not in lowered:
        return None

    tokens = lowered.replace("db", "").split()
    for t in tokens:
        try:
            value = float(t)
            return GainGoal(target_gain_db=value)
        except ValueError:
            continue

    return None
