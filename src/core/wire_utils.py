"""
Utilities for working with wires.
"""

from __future__ import annotations
from typing import Tuple, Optional
import math

from .schematic_model import SchematicWire


def point_to_line_distance(
    px: float, py: float,
    x1: float, y1: float,
    x2: float, y2: float
) -> Tuple[float, float, float]:
    """
    Calculate distance from a point to a line segment.
    
    Returns:
        (distance, closest_x, closest_y) where closest_x, closest_y is the closest point on the line segment.
    """
    dx = x2 - x1
    dy = y2 - y1
    
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        # Zero-length segment, just return distance to endpoint
        dist = math.sqrt((px - x1)**2 + (py - y1)**2)
        return dist, x1, y1
    
    # Calculate t (parameter along line segment)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    
    # Clamp t to [0, 1] to stay within segment
    t = max(0.0, min(1.0, t))
    
    # Calculate closest point on segment
    closest_x = x1 + t * dx
    closest_y = y1 + t * dy
    
    # Calculate distance
    dist = math.sqrt((px - closest_x)**2 + (py - closest_y)**2)
    
    return dist, closest_x, closest_y


def find_nearest_wire(
    wires: list[SchematicWire],
    x: float, y: float,
    max_dist: float = 15.0
) -> Tuple[Optional[SchematicWire], float, float]:
    """
    Find the wire closest to point (x, y).
    
    Returns:
        (wire, closest_x, closest_y) or (None, x, y) if no wire is close enough.
        closest_x, closest_y is the point on the wire closest to (x, y).
    """
    best_wire = None
    best_dist = max_dist
    best_point = (x, y)
    
    for wire in wires:
        dist, closest_x, closest_y = point_to_line_distance(
            x, y, wire.x1, wire.y1, wire.x2, wire.y2
        )
        
        if dist < best_dist:
            best_dist = dist
            best_wire = wire
            best_point = (closest_x, closest_y)
    
    return best_wire, best_point[0], best_point[1]


