from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

@dataclass
class SchematicPin:
    name: str
    x: float
    y: float
    net: Optional[str] = None


@dataclass
class SchematicComponent:
    ref: str
    ctype: str          # "R", "C", "OPAMP", "V", etc.
    value: float
    pins: List[SchematicPin]
    x: float
    y: float
    rotation: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)  # Additional properties (tolerance, ESR, model files, etc.)


@dataclass
class SchematicWire:
    x1: float
    y1: float
    x2: float
    y2: float
    net: str


@dataclass
class SchematicJunction:
    """
    Explicit junction node where wires intersect or connect.
    This represents a physical connection point in the circuit.
    """
    x: float
    y: float
    net: Optional[str] = None  # Net name assigned to this junction


@dataclass
class SchematicModel:
    components: List[SchematicComponent] = field(default_factory=list)
    wires: List[SchematicWire] = field(default_factory=list)
    junctions: List[SchematicJunction] = field(default_factory=list)
