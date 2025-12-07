"""
Automatic net extraction from schematic.

This module handles:
- Wire-wire intersection detection
- Junction node creation
- Net assignment and merging
- Validation that all pins belong to nets
"""

from __future__ import annotations
from typing import List, Set, Dict, Tuple, Optional
import math

from .schematic_model import (
    SchematicModel,
    SchematicWire,
    SchematicPin,
    SchematicComponent,
    SchematicJunction,
)


def find_line_intersection(
    x1: float, y1: float, x2: float, y2: float,
    x3: float, y3: float, x4: float, y4: float,
    tolerance: float = 1.0
) -> Optional[Tuple[float, float]]:
    """
    Find intersection point of two line segments.
    
    Returns (x, y) if segments intersect (excluding endpoints within tolerance),
    or None if they don't intersect.
    
    Args:
        tolerance: Minimum distance from endpoints to count as intersection.
                  This prevents intersections at wire endpoints (which are junctions).
    """
    # Calculate line segment parameters
    dx1 = x2 - x1
    dy1 = y2 - y1
    dx2 = x4 - x3
    dy2 = y4 - y3
    
    # Check if lines are parallel
    denominator = dx1 * dy2 - dy1 * dx2
    if abs(denominator) < 1e-10:
        return None  # Lines are parallel
    
    # Calculate intersection parameter
    t1 = ((x3 - x1) * dy2 - (y3 - y1) * dx2) / denominator
    t2 = ((x3 - x1) * dy1 - (y3 - y1) * dx1) / denominator
    
    # Check if intersection is within both line segments
    if 0 < t1 < 1 and 0 < t2 < 1:
        # Calculate intersection point
        ix = x1 + t1 * dx1
        iy = y1 + t1 * dy1
        
        # Check distance from endpoints to avoid intersections too close to endpoints
        dist1 = math.sqrt((ix - x1)**2 + (iy - y1)**2)
        dist2 = math.sqrt((ix - x2)**2 + (iy - y2)**2)
        dist3 = math.sqrt((ix - x3)**2 + (iy - y3)**2)
        dist4 = math.sqrt((ix - x4)**2 + (iy - y4)**2)
        
        min_dist = min(dist1, dist2, dist3, dist4)
        if min_dist >= tolerance:
            return (ix, iy)
    
    return None


def find_wire_intersections(wires: List[SchematicWire], tolerance: float = 1.0) -> List[Tuple[float, float, SchematicWire, SchematicWire]]:
    """
    Find all intersection points between wires.
    
    Returns list of (x, y, wire1, wire2) tuples for each intersection.
    """
    intersections = []
    
    for i, wire1 in enumerate(wires):
        for wire2 in wires[i+1:]:
            intersection = find_line_intersection(
                wire1.x1, wire1.y1, wire1.x2, wire1.y2,
                wire2.x1, wire2.y1, wire2.x2, wire2.y2,
                tolerance
            )
            if intersection:
                intersections.append((intersection[0], intersection[1], wire1, wire2))
    
    return intersections


def extract_nets_with_intersections(model: SchematicModel, tolerance: float = 10.0) -> None:
    """
    Perform automatic net extraction, handling:
    - Explicit junction nodes (created by user)
    - Pin-wire connections
    - Net merging
    - Ground components: All GND components are treated as connected to the same ground net
    
    Note: Wire-wire intersections are NOT automatically connected.
    Users must explicitly create junctions to connect wires.
    
    Args:
        model: SchematicModel to extract nets from
        tolerance: Distance tolerance for grouping points (pixels)
    """
    # FIRST: Ensure all GND components and their pins are assigned net "0"
    # This must happen before connectivity graph building, because ground is a global reference
    for comp in model.components:
        if comp.ctype == "GND":
            for pin in comp.pins:
                pin.net = "0"
    
    # Keep existing junctions - don't clear them
    # Junctions are created explicitly by the user
    
    # Build a map of existing junctions by position
    junction_positions: Dict[Tuple[float, float], SchematicJunction] = {}
    for junction in model.junctions:
        x_rounded = round(junction.x / tolerance) * tolerance
        y_rounded = round(junction.y / tolerance) * tolerance
        junction_positions[(x_rounded, y_rounded)] = junction
    
    # Build connectivity graph: what's connected to what
    # We'll use object IDs as keys since dataclasses aren't hashable
    connectivity: Dict[int, Set[int]] = {}  # Object ID -> set of connected object IDs
    id_to_obj: Dict[int, object] = {}  # Object ID -> object (for later retrieval)
    
    def get_obj_id(obj):
        """Get unique ID for an object and store mapping."""
        obj_id = id(obj)
        id_to_obj[obj_id] = obj
        return obj_id
    
    def add_connection(obj1, obj2):
        """Mark two objects as connected."""
        id1 = get_obj_id(obj1)
        id2 = get_obj_id(obj2)
        
        if id1 not in connectivity:
            connectivity[id1] = set()
        if id2 not in connectivity:
            connectivity[id2] = set()
        connectivity[id1].add(id2)
        connectivity[id2].add(id1)
    
    # Add wire-junction connections (wire connects to explicit junction)
    for wire in model.wires:
        for x_rounded, y_rounded in junction_positions.keys():
            # Check if wire passes through or ends at junction
            dist1 = math.sqrt((wire.x1 - x_rounded)**2 + (wire.y1 - y_rounded)**2)
            dist2 = math.sqrt((wire.x2 - x_rounded)**2 + (wire.y2 - y_rounded)**2)
            # Also check if wire passes through junction (not just endpoints)
            # Project junction point onto wire line
            dx = wire.x2 - wire.x1
            dy = wire.y2 - wire.y1
            if abs(dx) < 1e-6 and abs(dy) < 1e-6:
                continue  # Zero-length wire
            wire_len_sq = dx*dx + dy*dy
            t = ((x_rounded - wire.x1)*dx + (y_rounded - wire.y1)*dy) / wire_len_sq
            if 0 <= t <= 1:
                proj_x = wire.x1 + t*dx
                proj_y = wire.y1 + t*dy
                dist_to_line = math.sqrt((x_rounded - proj_x)**2 + (y_rounded - proj_y)**2)
                if dist_to_line <= tolerance or dist1 <= tolerance or dist2 <= tolerance:
                    junction = junction_positions[(x_rounded, y_rounded)]
                    add_connection(wire, junction)
    
    # Add wire-pin connections (pin is at wire endpoint or on wire segment)
    for comp in model.components:
        for pin in comp.pins:
            for wire in model.wires:
                # Check if pin is at wire endpoint
                dist1 = math.sqrt((wire.x1 - pin.x)**2 + (wire.y1 - pin.y)**2)
                dist2 = math.sqrt((wire.x2 - pin.x)**2 + (wire.y2 - pin.y)**2)
                
                # Also check if pin is on the wire segment (for Manhattan routing)
                # Project pin onto wire line and check distance
                dx = wire.x2 - wire.x1
                dy = wire.y2 - wire.y1
                wire_len_sq = dx*dx + dy*dy
                
                is_on_segment = False
                if wire_len_sq > 1e-6:  # Non-zero length wire
                    t = ((pin.x - wire.x1)*dx + (pin.y - wire.y1)*dy) / wire_len_sq
                    if 0 <= t <= 1:  # Projection is within segment
                        proj_x = wire.x1 + t*dx
                        proj_y = wire.y1 + t*dy
                        dist_to_line = math.sqrt((pin.x - proj_x)**2 + (pin.y - proj_y)**2)
                        if dist_to_line <= tolerance:
                            is_on_segment = True
                
                # Connect if pin is at endpoint or on segment
                if dist1 <= tolerance or dist2 <= tolerance or is_on_segment:
                    add_connection(pin, wire)
                    # Also connect to junction if pin is at junction position
                    x_rounded = round(pin.x / tolerance) * tolerance
                    y_rounded = round(pin.y / tolerance) * tolerance
                    junction = junction_positions.get((x_rounded, y_rounded))
                    if junction:
                        add_connection(pin, junction)
    
    # Add pin-junction connections (pin is at junction)
    for comp in model.components:
        for pin in comp.pins:
            x_rounded = round(pin.x / tolerance) * tolerance
            y_rounded = round(pin.y / tolerance) * tolerance
            junction = junction_positions.get((x_rounded, y_rounded))
            if junction:
                add_connection(pin, junction)
    
    # Add wire-wire connections (wires that share endpoints or are very close)
    for i, wire1 in enumerate(model.wires):
        for wire2 in model.wires[i+1:]:
            # Check if wires share an endpoint
            dist_1_1 = math.sqrt((wire1.x1 - wire2.x1)**2 + (wire1.y1 - wire2.y1)**2)
            dist_1_2 = math.sqrt((wire1.x1 - wire2.x2)**2 + (wire1.y1 - wire2.y2)**2)
            dist_2_1 = math.sqrt((wire1.x2 - wire2.x1)**2 + (wire1.y2 - wire2.y1)**2)
            dist_2_2 = math.sqrt((wire1.x2 - wire2.x2)**2 + (wire1.y2 - wire2.y2)**2)
            
            # If any endpoints are close (within tolerance), connect the wires
            if dist_1_1 <= tolerance or dist_1_2 <= tolerance or dist_2_1 <= tolerance or dist_2_2 <= tolerance:
                add_connection(wire1, wire2)
    
    # SPECIAL CASE: Connect all GND component pins together
    # All ground components share the same ground net, so their pins should be connected
    gnd_pins = []
    for comp in model.components:
        if comp.ctype == "GND":
            for pin in comp.pins:
                gnd_pins.append(pin)
    
    # Connect all GND pins to each other (they're all on the same ground net)
    for i, pin1 in enumerate(gnd_pins):
        for pin2 in gnd_pins[i+1:]:
            add_connection(pin1, pin2)
    
    # Find connected components using DFS to merge nets
    visited: Set[int] = set()
    net_groups: List[List[object]] = []  # List of groups, each group is a list of objects
    
    def dfs(obj_id, current_group_ids):
        """Depth-first search to find all connected objects."""
        if obj_id in visited:
            return
        visited.add(obj_id)
        current_group_ids.add(obj_id)
        for neighbor_id in connectivity.get(obj_id, set()):
            dfs(neighbor_id, current_group_ids)
    
    # Find all connected groups
    all_object_ids: Set[int] = set()
    for wire in model.wires:
        all_object_ids.add(get_obj_id(wire))
    for junction in model.junctions:
        all_object_ids.add(get_obj_id(junction))
    for comp in model.components:
        for pin in comp.pins:
            all_object_ids.add(get_obj_id(pin))
    
    for obj_id in all_object_ids:
        if obj_id not in visited:
            group_ids = set()
            dfs(obj_id, group_ids)
            if group_ids:
                # Convert group IDs back to objects
                group_objects = [id_to_obj[gid] for gid in group_ids if gid in id_to_obj]
                if group_objects:
                    net_groups.append(group_objects)
    
    # Assign net names to each group
    # Priority: ground nets ("0", "GND") > existing pin nets > existing wire nets > junctions > auto-generated
    net_counter = 1
    ground_nets = {"0", "GND", "gnd"}
    
    # First pass: identify which groups contain ground
    ground_groups = set()
    for i, group in enumerate(net_groups):
        for obj in group:
            if isinstance(obj, SchematicPin) and obj.net and obj.net.upper() in ground_nets:
                ground_groups.add(i)
                break
            if isinstance(obj, SchematicWire) and obj.net and obj.net.upper() in ground_nets:
                ground_groups.add(i)
                break
            if isinstance(obj, SchematicJunction) and obj.net and obj.net.upper() in ground_nets:
                ground_groups.add(i)
                break
    
    for i, group in enumerate(net_groups):
        # Try to find an existing net name in the group
        net_name = None
        is_ground_group = (i in ground_groups)
        
        if is_ground_group:
            # This group contains ground - assign it net "0"
            net_name = "0"
        else:
            # For non-ground groups, always assign a FRESH unique net name
            # Don't reuse existing net names from objects because they may be incorrect
            # from previous net extraction passes. This ensures each group gets its own
            # distinct net name based on the connectivity graph.
            
            # Generate next available net number sequentially
            # (net_counter already starts at 1, so this will generate N001, N002, etc.)
            net_name = f"N{net_counter:03d}"
            net_counter += 1
        
        # Assign net to all objects in group
        for obj in group:
            if isinstance(obj, SchematicPin):
                obj.net = net_name
            elif isinstance(obj, SchematicWire):
                obj.net = net_name
            elif isinstance(obj, SchematicJunction):
                obj.net = net_name


def validate_all_pins_have_nets(model: SchematicModel) -> Tuple[bool, List[str]]:
    """
    Validate that all component pins belong to a net.
    
    Returns:
        (is_valid, list_of_unconnected_pin_descriptions)
    """
    unconnected = []
    
    for comp in model.components:
        for pin in comp.pins:
            if pin.net is None or pin.net == "":
                unconnected.append(f"{comp.ref}.{pin.name} at ({pin.x:.1f}, {pin.y:.1f})")
    
    return (len(unconnected) == 0, unconnected)

