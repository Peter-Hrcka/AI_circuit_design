"""
AI orchestration layer.

Later:
- This will call a real LLM (local or remote).
- It will also reason about topology changes, noise, THD, etc.

Now:
- It parses a simple gain goal and calls the non-inverting optimizer.
"""

from __future__ import annotations
from typing import Tuple

from core.circuit import Circuit
from core.optimization import optimize_gain_for_non_inverting_stage
from .goals import parse_goal, GainGoal


def apply_text_goal_to_circuit(
    circuit: Circuit,
    goal_text: str,
) -> Tuple[Circuit, str]:
    """
    Interpret the user text goal and apply a simple optimization.

    Returns:
    - updated circuit
    - human-readable summary of what was done
    """
    goal = parse_goal(goal_text)
    if goal is None:
        return circuit, "Goal not recognized. For now, try e.g. 'set gain to 40 dB'."

    assert isinstance(goal, GainGoal)
    optimized_circuit, achieved_gain_db = optimize_gain_for_non_inverting_stage(
        circuit,
        target_gain_db=goal.target_gain_db,
    )

    msg = (
        f"Adjusted R1 to achieve approximately {achieved_gain_db:.2f} dB "
        f"(target was {goal.target_gain_db:.2f} dB, ideal op-amp model)."
    )
    return optimized_circuit, msg
