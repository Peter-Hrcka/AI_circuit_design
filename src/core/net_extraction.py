"""
Automatic net extraction from schematic.

This module handles:
- Wire-wire intersection detection
- Junction node creation
- Net assignment and merging
- Validation that all pins belong to nets
- Net name normalization (canonical form)
"""

from __future__ import annotations
from typing import List, Set, Dict, Tuple, Optional
import math


def normalize_net_name(name: Optional[str]) -> Optional[str]:
    """
    Normalize net/node name to canonical form.
    
    Canonical rule:
    - All net/node names are normalized to UPPERCASE
    - Ground stays "0" (do not rename to "GND")
    - None stays None
    
    This is the single source of truth for net name normalization.
    Use this everywhere instead of calling .upper() ad-hoc.
    
    Args:
        name: Net name to normalize (can be None)
    
    Returns:
        Normalized net name (UPPERCASE, except "0" stays "0"), or None if input is None
    """
    if name is None:
        return None
    name = name.strip()
    if name == "0":
        return "0"
    return name.upper()

from .schematic_model import (
    SchematicModel,
    SchematicWire,
    SchematicPin,
    SchematicComponent,
    SchematicJunction,
    SchematicNetLabel,
)
from .wire_utils import wire_segments, point_segment_distance


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
    Find all intersection points between wires by checking all segments.
    
    Returns list of (x, y, wire1, wire2) tuples for each intersection.
    """
    from .wire_utils import wire_segments
    
    intersections = []
    
    for i, wire1 in enumerate(wires):
        for wire2 in wires[i+1:]:
            # Check all segments of both wires
            for (x1a, y1a), (x2a, y2a) in wire_segments(wire1):
                for (x1b, y1b), (x2b, y2b) in wire_segments(wire2):
                    intersection = find_line_intersection(
                        x1a, y1a, x2a, y2a,
                        x1b, y1b, x2b, y2b,
                        tolerance
                    )
                    if intersection:
                        intersections.append((intersection[0], intersection[1], wire1, wire2))
    
    return intersections


def extract_nets_with_intersections(model: SchematicModel, tolerance: float = 2.0) -> None:
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
    # Check all segments of each wire polyline
    for wire in model.wires:
        for x_rounded, y_rounded in junction_positions.keys():
            junction = junction_positions[(x_rounded, y_rounded)]
            # Check all segments of the wire polyline
            for (x1, y1), (x2, y2) in wire_segments(wire):
                # Check if junction is at segment endpoint
                dist1 = math.sqrt((x1 - x_rounded)**2 + (y1 - y_rounded)**2)
                dist2 = math.sqrt((x2 - x_rounded)**2 + (y2 - y_rounded)**2)
                if dist1 <= tolerance or dist2 <= tolerance:
                    add_connection(wire, junction)
                    break
                # Check if junction is on segment
                dist = point_segment_distance(x_rounded, y_rounded, x1, y1, x2, y2)
                if dist <= tolerance:
                    add_connection(wire, junction)
                    break
    
    # Add wire-pin connections (pin is at wire endpoint or on wire segment)
    # Check all segments of each wire polyline
    for comp in model.components:
        for pin in comp.pins:
            for wire in model.wires:
                # Check all segments of the wire polyline
                for (x1, y1), (x2, y2) in wire_segments(wire):
                    # Check if pin is at segment endpoint
                    dist1 = math.sqrt((x1 - pin.x)**2 + (y1 - pin.y)**2)
                    dist2 = math.sqrt((x2 - pin.x)**2 + (y2 - pin.y)**2)
                    if dist1 <= tolerance or dist2 <= tolerance:
                        add_connection(pin, wire)
                        break
                    # Check if pin is on segment
                    dist = point_segment_distance(pin.x, pin.y, x1, y1, x2, y2)
                    if dist <= tolerance:
                        add_connection(pin, wire)
                        break
                
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
    
    # Add wire-wire connections (wires that share endpoints or intersect)
    # Check all segments of both wires
    for i, wire1 in enumerate(model.wires):
        for wire2 in model.wires[i+1:]:
            connected = False
            # Check if any endpoints are close (within tolerance)
            for pt1 in wire1.points:
                for pt2 in wire2.points:
                    dist = math.sqrt((pt1[0] - pt2[0])**2 + (pt1[1] - pt2[1])**2)
                    if dist <= tolerance:
                        add_connection(wire1, wire2)
                        connected = True
                        break
                if connected:
                    break
            
            # Also check for segment intersections (for crossing wires with junctions)
            if not connected:
                for (x1a, y1a), (x2a, y2a) in wire_segments(wire1):
                    for (x1b, y1b), (x2b, y2b) in wire_segments(wire2):
                        # Check if segments share an endpoint
                        if (abs(x1a - x1b) < tolerance and abs(y1a - y1b) < tolerance) or \
                           (abs(x1a - x2b) < tolerance and abs(y1a - y2b) < tolerance) or \
                           (abs(x2a - x1b) < tolerance and abs(y2a - y1b) < tolerance) or \
                           (abs(x2a - x2b) < tolerance and abs(y2a - y2b) < tolerance):
                            add_connection(wire1, wire2)
                            connected = True
                            break
                        # Check for intersection (for Manhattan routing, this is simple)
                        # For now, only connect if endpoints coincide (junctions required for crossings)
                    if connected:
                        break
    
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
    
    # Add label connections to nearby objects (pins, junctions, wires)
    for label in model.net_labels:
        # Connect label to nearby pins
        for comp in model.components:
            for pin in comp.pins:
                dist = math.sqrt((label.x - pin.x)**2 + (label.y - pin.y)**2)
                if dist <= tolerance:
                    add_connection(label, pin)
        
        # Connect label to nearby junctions
        for junction in model.junctions:
            dist = math.sqrt((label.x - junction.x)**2 + (label.y - junction.y)**2)
            if dist <= tolerance:
                add_connection(label, junction)
        
        # Connect label to nearby wires (on any wire segment)
        for wire in model.wires:
            for (x1, y1), (x2, y2) in wire_segments(wire):
                dist = point_segment_distance(label.x, label.y, x1, y1, x2, y2)
                if dist <= tolerance:
                    add_connection(label, wire)
                    break
    
    # Global merge: connect labels with the same name to each other
    # Normalize label names (strip spaces, use uppercase for comparison, but keep original for display)
    label_groups: Dict[str, List[SchematicNetLabel]] = {}
    for label in model.net_labels:
        # Use uppercase, stripped name for grouping (but keep original name in label.name)
        key = label.name.strip().upper()
        if key:  # Only group non-empty names
            if key not in label_groups:
                label_groups[key] = []
            label_groups[key].append(label)
    
    # Connect all labels in each group to each other
    for label_list in label_groups.values():
        for i, label1 in enumerate(label_list):
            for label2 in label_list[i+1:]:
                add_connection(label1, label2)
    
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
    for label in model.net_labels:
        all_object_ids.add(get_obj_id(label))
    
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
    # A group is ground ONLY if it contains a GND component pin, not just a "0" label
    ground_groups = set()
    for i, group in enumerate(net_groups):
        for obj in group:
            # Check if this is a pin belonging to a GND component
            if isinstance(obj, SchematicPin):
                # Find the component this pin belongs to
                for comp in model.components:
                    if comp.ctype == "GND" and obj in comp.pins:
                        ground_groups.add(i)
                        break
                if i in ground_groups:
                    break
    
    for i, group in enumerate(net_groups):
        # Try to find an existing net name in the group
        net_name = None
        is_ground_group = (i in ground_groups)
        
        if is_ground_group:
            # This group contains ground - assign it net "0"
            net_name = "0"
        else:
            # Check if group contains any net labels - use label name as net name
            # BUT: if label name is "0" or "GND", only use it if group is actually connected to ground
            label_names = []
            for obj in group:
                if isinstance(obj, SchematicNetLabel):
                    if obj.name and obj.name.strip():  # Non-empty label name
                        label_name = obj.name.strip()
                        # Don't use "0" or "GND" labels unless this is actually a ground group
                        if label_name.upper() in ground_nets and not is_ground_group:
                            # Skip this label - it's incorrectly placed or the group isn't actually ground
                            continue
                        label_names.append(label_name)
            
            if label_names:
                # Use label name for net (if multiple different names, pick first sorted)
                unique_names = sorted(set(label_names))
                if len(unique_names) > 1:
                    # Conflict: multiple different label names in same group
                    print(f"Warning: Net group contains multiple different label names: {unique_names}. Using '{unique_names[0]}'.")
                net_name = unique_names[0]
            else:
                # For non-ground groups without labels, always assign a FRESH unique net name
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
            # Note: labels don't need net assignment, they're just used for naming


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

