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
    """
    Wire represented as a polyline (list of points).
    """
    points: list[tuple[float, float]]  # polyline points, length >= 2
    net: str = "?"
    
    def __post_init__(self):
        """Validate and clean up points."""
        # Ensure points is a list and has at least 2 points
        if not isinstance(self.points, list) or len(self.points) < 2:
            raise ValueError(f"SchematicWire must have at least 2 points, got {self.points}")
        
        # De-duplicate consecutive identical points
        deduplicated = [self.points[0]]
        for pt in self.points[1:]:
            if pt != deduplicated[-1]:
                deduplicated.append(pt)
        self.points = deduplicated
        
        # Ensure we still have at least 2 points after deduplication
        if len(self.points) < 2:
            raise ValueError("SchematicWire must have at least 2 distinct points")
    
    @property
    def x1(self) -> float:
        """Backwards compatibility: return first point x."""
        return self.points[0][0]
    
    @x1.setter
    def x1(self, value: float) -> None:
        """Backwards compatibility: set first point x, preserving y."""
        self.points[0] = (float(value), self.points[0][1])
    
    @property
    def y1(self) -> float:
        """Backwards compatibility: return first point y."""
        return self.points[0][1]
    
    @y1.setter
    def y1(self, value: float) -> None:
        """Backwards compatibility: set first point y, preserving x."""
        self.points[0] = (self.points[0][0], float(value))
    
    @property
    def x2(self) -> float:
        """Backwards compatibility: return last point x."""
        return self.points[-1][0]
    
    @x2.setter
    def x2(self, value: float) -> None:
        """Backwards compatibility: set last point x, preserving y."""
        self.points[-1] = (float(value), self.points[-1][1])
    
    @property
    def y2(self) -> float:
        """Backwards compatibility: return last point y."""
        return self.points[-1][1]
    
    @y2.setter
    def y2(self, value: float) -> None:
        """Backwards compatibility: set last point y, preserving x."""
        self.points[-1] = (self.points[-1][0], float(value))


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
class SchematicNetLabel:
    """
    User-placed net label that connects points with the same label name.
    """
    x: float
    y: float
    name: str


@dataclass
class SchematicModel:
    components: List[SchematicComponent] = field(default_factory=list)
    wires: List[SchematicWire] = field(default_factory=list)
    junctions: List[SchematicJunction] = field(default_factory=list)
    net_labels: List[SchematicNetLabel] = field(default_factory=list)
