"""
Core data structures for circuits and components.

This is intentionally simple for the MVP:
- Only 2-terminal components + an "opamp" block
- No transistor models yet
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class Component:
    """
    Generic circuit component.

    Examples:
    - R1 between nodes "in" and "out" with value=10k, unit="ohm"
    - C1 between "out" and "0" with value=100n, unit="F"
    - U1 op-amp between nodes "noninv", "inv", "out"
      (for now we may store op-amp as a "block" with a few extra fields)
    """
    ref: str           # e.g. "R1"
    ctype: str         # e.g. "R", "C", "L", "OPAMP"
    node1: str
    node2: str
    value: float       # numerical value (e.g. 10000.0)
    unit: str = ""     # purely informational for now
    extra: Dict[str, float] = field(default_factory=dict)


@dataclass
class Circuit:
    """
    Minimal representation of a circuit.

    For MVP:
    - list of components
    - optional metadata (e.g. for SPICE options, comments, etc.)
    """
    name: str
    components: List[Component] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)

    def add_component(self, component: Component) -> None:
        self.components.append(component)

    def get_component(self, ref: str) -> Optional[Component]:
        for c in self.components:
            if c.ref == ref:
                return c
        return None

    def as_dict(self) -> Dict:
        """Convenience helper for debugging / future JSON export."""
        return {
            "name": self.name,
            "components": [vars(c) for c in self.components],
            "metadata": dict(self.metadata),
        }
