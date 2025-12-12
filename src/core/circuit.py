"""
Core data structures for circuits and components.

Supports a comprehensive set of circuit components:
- Passive: R (resistor), C (capacitor), L (inductor)
- Diodes: D (diode)
- Transistors: Q (BJT), M (MOSFET)
- Sources: V (voltage source), I (current source), G (VCCS)
- Active: OPAMP (operational amplifier)
- Markers: GND (ground), VOUT (output marker)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Union


@dataclass
class Component:
    """
    Generic circuit component.

    Supported component types (ctype):
    - "R": Resistor - 2-terminal, value in ohms
    - "C": Capacitor - 2-terminal, value in farads
    - "L": Inductor - 2-terminal, value in henries
    - "D": Diode - 2-terminal (anode/cathode), value unused, extra["model"] for SPICE model
    - "Q": BJT transistor - 3-terminal (collector, base, emitter)
        node1=collector, node2=emitter, extra["base_node"]=base
        extra["polarity"]="NPN" or "PNP", extra["model"] for SPICE model
    - "M": MOSFET - 3-terminal (drain, gate, source, bulk=source internally)
        node1=drain, node2=source, extra["gate_node"]=gate
        extra["mos_type"]="NMOS" or "PMOS", extra["model"] for SPICE model
        Note: Bulk is internally set to source for 3-terminal MOSFETs
    - "M_bulk": MOSFET - 4-terminal (drain, gate, source, bulk)
        node1=drain, node2=source, extra["gate_node"]=gate, extra["bulk_node"]=bulk (defaults to source)
        extra["mos_type"]="NMOS" or "PMOS", extra["model"] for SPICE model
    - "V": Voltage source - 2-terminal, value in volts
    - "I": Current source - 2-terminal, value in amperes
    - "G": Voltage-controlled current source (VCCS) - 4-terminal
        node1=output positive, node2=output negative
        extra["ctrl_p"]=control voltage positive, extra["ctrl_n"]=control voltage negative
        value = transconductance in siemens
    - "OPAMP": Operational amplifier - 5-terminal (non-inverting, inverting, output, VCC, VEE)
        node1=non-inverting, node2=inverting, extra["output_node"]=output
        extra["vcc_node"]=VCC node, extra["vee_node"]=VEE node (or use extra["vcc"]/extra["vee"] for voltage values)
    - "OPAMP_ideal": Operational amplifier - 3-terminal (non-inverting, inverting, output, ideal/no supply pins)
        node1=non-inverting, node2=inverting, extra["output_node"]=output
        extra["vcc"] and extra["vee"] are optional voltage values for supply rails (defaults: 15V, -15V)
    - "GND": Ground marker - single terminal
    - "VOUT": Output marker - single terminal

    Examples:
    - R1 between nodes "in" and "out" with value=10k, unit="ohm"
    - C1 between "out" and "0" with value=100n, unit="F"
    - L1 between nodes "N001" and "N002" with value=10m, unit="H"
    - D1 anode/cathode with extra["model"]="DDEFAULT"
    - Q1 BJT with node1=collector, node2=emitter, extra["base_node"]=base, extra["polarity"]="NPN"
    - M1 3-terminal MOSFET with node1=drain, node2=source, extra["gate_node"]=gate, extra["mos_type"]="NMOS"
    - M1_bulk 4-terminal MOSFET with node1=drain, node2=source, extra["gate_node"]=gate, extra["bulk_node"]=bulk, extra["mos_type"]="NMOS"
    - G1 VCCS with node1=np, node2=nn, extra["ctrl_p"]=vp, extra["ctrl_n"]=vn, value=transconductance
    - U1 op-amp (OPAMP) with node1=non-inverting, node2=inverting, extra["output_node"]=output, extra["vcc_node"]=VCC, extra["vee_node"]=VEE
    - U1 op-amp (OPAMP_ideal) with node1=non-inverting, node2=inverting, extra["output_node"]=output
    """
    ref: str           # e.g. "R1"
    ctype: str         # e.g. "R", "C", "L", "D", "Q", "M", "M_bulk", "V", "I", "G", "OPAMP", "OPAMP_ideal"
    node1: str         # First terminal/node
    node2: str         # Second terminal/node (or emitter for BJT, source for MOSFET)
    value: float       # numerical value (e.g. 10000.0 for resistor, transconductance for VCCS)
    unit: str = ""     # Unit string: "ohm", "F", "H", "V", "A", "S" (siemens)
    extra: Dict[str, Union[float, str]] = field(default_factory=dict)  # Additional properties (model names, node references, etc.)


@dataclass
class Circuit:
    """
    Representation of a circuit topology.

    Contains:
    - list of components (R, C, L, D, Q, M, V, I, G, OPAMP, etc.)
    - optional metadata (e.g. for SPICE options, op-amp model files, comments, etc.)
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
