"""
Schematic validation and error checking.

This module provides pre-simulation validation checks:
- Unconnected pins
- Non-ground reference points
- Floating subcircuits
- Short circuits
"""

from __future__ import annotations
from typing import List, Tuple, Set, Dict, Optional
from dataclasses import dataclass

from .schematic_model import (
    SchematicModel,
    SchematicComponent,
    SchematicPin,
    SchematicWire,
    SchematicJunction,
)


@dataclass
class ValidationError:
    """Represents a validation error with a message and severity."""
    message: str
    severity: str  # "error" or "warning"
    component_ref: Optional[str] = None
    pin_name: Optional[str] = None


def validate_schematic(model: SchematicModel) -> Tuple[bool, List[ValidationError]]:
    """
    Perform comprehensive validation of a schematic model.
    
    Checks:
    1. Unconnected pins
    2. Non-ground reference points (missing ground)
    3. Floating subcircuits (components not connected to rest of circuit)
    4. Short circuits (output directly to ground, etc.)
    
    Returns:
        (is_valid, list_of_errors)
    """
    errors: List[ValidationError] = []
    
    # First, ensure nets are extracted
    from .net_extraction import extract_nets_with_intersections, validate_all_pins_have_nets
    extract_nets_with_intersections(model)
    
    # 1. Check for unconnected pins
    is_valid, unconnected = validate_all_pins_have_nets(model)
    if not is_valid:
        for desc in unconnected:
            errors.append(ValidationError(
                message=f"Unconnected pin: {desc}",
                severity="error",
            ))
    
    # 2. Check for ground reference
    has_ground = _check_ground_reference(model)
    if not has_ground:
        errors.append(ValidationError(
            message="No ground reference found. Circuit must have at least one ground node (GND or 0).",
            severity="error",
        ))
    
    # 3. Check for floating subcircuits
    floating = _find_floating_subcircuits(model)
    for comp_refs in floating:
        comp_list = ", ".join(comp_refs)
        errors.append(ValidationError(
            message=f"Floating subcircuit: Components {comp_list} are not connected to the rest of the circuit.",
            severity="error",
        ))
    
    # 4. Check for short circuits
    shorts = _find_short_circuits(model)
    for short_desc in shorts:
        errors.append(ValidationError(
            message=short_desc,
            severity="error",
        ))
    
    return (len(errors) == 0, errors)


def _check_ground_reference(model: SchematicModel) -> bool:
    """
    Check if the circuit has at least one ground reference.
    
    Ground can be:
    - A GND component
    - A net named "GND" or "0"
    """
    # Check for GND components
    for comp in model.components:
        if comp.ctype == "GND":
            return True
    
    # Check for ground nets
    ground_nets = {"GND", "0", "gnd"}
    for comp in model.components:
        for pin in comp.pins:
            if pin.net and pin.net.upper() in ground_nets:
                return True
    
    # Check wires
    for wire in model.wires:
        if wire.net and wire.net.upper() in ground_nets:
            return True
    
    # Check junctions
    for junction in model.junctions:
        if junction.net and junction.net.upper() in ground_nets:
            return True
    
    return False


def _find_floating_subcircuits(model: SchematicModel) -> List[List[str]]:
    """
    Find components that form isolated subcircuits not connected to the main circuit.
    
    A component is considered "floating" if it has no connections to other components
    (only connected to ground or completely unconnected).
    
    Returns list of lists, where each inner list contains component refs that form
    a floating subcircuit.
    """
    # Build connectivity graph: which components are connected via nets
    # Two components are connected if they share a net (excluding ground)
    comp_to_nets: Dict[str, Set[str]] = {}
    net_to_comps: Dict[str, Set[str]] = {}
    
    ground_nets = {"GND", "0", "gnd", ""}
    
    for comp in model.components:
        comp_nets = set()
        for pin in comp.pins:
            if pin.net and pin.net.upper() not in ground_nets:
                comp_nets.add(pin.net.upper())
        comp_to_nets[comp.ref] = comp_nets
        
        # Build reverse mapping
        for net in comp_nets:
            if net not in net_to_comps:
                net_to_comps[net] = set()
            net_to_comps[net].add(comp.ref)
    
    # Find connected components using DFS
    visited: Set[str] = set()
    components_by_group: List[Set[str]] = []
    
    def dfs(comp_ref: str, current_group: Set[str]):
        """Depth-first search to find all connected components."""
        if comp_ref in visited:
            return
        visited.add(comp_ref)
        current_group.add(comp_ref)
        
        # Find all components connected via shared nets
        comp_nets = comp_to_nets.get(comp_ref, set())
        for net in comp_nets:
            connected_comps = net_to_comps.get(net, set())
            for connected_comp in connected_comps:
                if connected_comp != comp_ref:
                    dfs(connected_comp, current_group)
    
    # Track which components have ground connections
    comps_with_ground: Set[str] = set()
    has_any_ground_in_circuit = False
    
    for comp in model.components:
        for pin in comp.pins:
            if pin.net and pin.net.upper() in ground_nets:
                comps_with_ground.add(comp.ref)
                has_any_ground_in_circuit = True
                break
    
    # Find all groups (components connected via non-ground nets)
    for comp_ref in comp_to_nets.keys():
        if comp_ref not in visited:
            group = set()
            dfs(comp_ref, group)
            if group:
                components_by_group.append(group)
    
    # Check for components with no connections at all (completely unconnected)
    floating_single_comps: List[str] = []
    
    for comp in model.components:
        # Skip GND components entirely - they're reference points, never floating
        if comp.ctype == "GND":
            continue
        
        # Check if component has any pins with nets assigned
        has_any_net = False
        has_only_ground = True
        
        for pin in comp.pins:
            if pin.net and pin.net.strip():
                has_any_net = True
                # Check if this is a non-ground net
                if pin.net.upper() not in ground_nets:
                    has_only_ground = False
                    break
        
        # If component has no nets at all and wasn't visited, it's floating
        if not has_any_net and comp.ref not in visited:
            floating_single_comps.append(comp.ref)
        # If component only has ground nets and wasn't visited:
        elif has_only_ground and has_any_net and comp.ref not in visited:
            # Components with only ground nets are NOT floating if:
            # - There are other components in the circuit (they share ground reference)
            # They're only floating if they're the ONLY component in the circuit
            if len(model.components) == 1:
                floating_single_comps.append(comp.ref)
            # Otherwise, if there are other components, they're connected through shared ground
    
    # If there's only one group (or none) and no floating single components, no floating subcircuits
    if len(components_by_group) <= 1 and not floating_single_comps:
        return []
    
    # Find the largest group (assumed to be the main circuit)
    # BUT: Also consider components with ground as connected to the main circuit
    # if there are any ground connections in the circuit
    floating_groups: List[List[str]] = []
    
    if len(components_by_group) > 1:
        if components_by_group:
            main_group = max(components_by_group, key=len)
            # All other groups are potentially floating
            for group in components_by_group:
                if group != main_group:
                    # Check if this group has ground connections
                    # If so, and if there are ground connections elsewhere, it's connected through ground
                    group_has_ground = any(comp_ref in comps_with_ground for comp_ref in group)
                    if group_has_ground and has_any_ground_in_circuit:
                        # This group is connected through ground, not floating
                        continue
                    # Otherwise, it's floating
                    floating_groups.append(list(group))
    
    # Add single floating components (completely unconnected, or only ground but isolated)
    if floating_single_comps:
        floating_groups.append(floating_single_comps)
    
    return floating_groups


def _find_short_circuits(model: SchematicModel) -> List[str]:
    """
    Find short circuits in the schematic.
    
    Checks for:
    - Output nodes directly connected to ground
    - Voltage source shorted (both terminals on same net)
    - Other obvious shorts
    
    Returns list of error messages describing shorts.
    """
    shorts: List[str] = []
    
    # Find output nodes (from VOUT markers or op-amp outputs)
    output_nets: Set[str] = set()
    ground_nets = {"GND", "0", "gnd", ""}
    
    for comp in model.components:
        if comp.ctype == "VOUT":
            for pin in comp.pins:
                if pin.net:
                    output_nets.add(pin.net.upper())
        
        elif comp.ctype == "OPAMP":
            for pin in comp.pins:
                if pin.name.upper() in ("OUT", "OUTPUT") and pin.net:
                    output_nets.add(pin.net.upper())
    
    # Check if output nodes are connected to ground
    for comp in model.components:
        for pin in comp.pins:
            if pin.net and pin.net.upper() in output_nets:
                # Check if this net is also ground
                if pin.net.upper() in ground_nets:
                    shorts.append(f"Output node '{pin.net}' is shorted to ground.")
                # Check if connected to GND component
                for other_comp in model.components:
                    if other_comp.ctype == "GND":
                        for gnd_pin in other_comp.pins:
                            if gnd_pin.net and gnd_pin.net.upper() == pin.net.upper():
                                shorts.append(f"Output node '{pin.net}' is shorted to ground via {other_comp.ref}.")
    
    # Check voltage sources for shorts (both terminals on same net)
    for comp in model.components:
        if comp.ctype == "V":
            if len(comp.pins) == 2:
                net1 = comp.pins[0].net
                net2 = comp.pins[1].net
                if net1 and net2 and net1.upper() == net2.upper():
                    shorts.append(f"Voltage source {comp.ref} is shorted: both terminals on net '{net1}'.")
    
    # Check current sources for shorts (both terminals on same net)
    for comp in model.components:
        if comp.ctype == "I":
            if len(comp.pins) == 2:
                net1 = comp.pins[0].net
                net2 = comp.pins[1].net
                if net1 and net2 and net1.upper() == net2.upper():
                    shorts.append(f"Current source {comp.ref} is shorted: both terminals on net '{net1}'.")
    
    # Check for direct wire shorts (wire connecting output to ground)
    for wire in model.wires:
        wire_net = wire.net.upper() if wire.net else ""
        if wire_net in output_nets:
            # Check if wire endpoints are at ground
            for comp in model.components:
                if comp.ctype == "GND":
                    for gnd_pin in comp.pins:
                        # Check if wire connects to ground pin
                        dist1 = ((wire.x1 - gnd_pin.x)**2 + (wire.y1 - gnd_pin.y)**2)**0.5
                        dist2 = ((wire.x2 - gnd_pin.x)**2 + (wire.y2 - gnd_pin.y)**2)**0.5
                        if dist1 < 5.0 or dist2 < 5.0:
                            shorts.append(f"Output net '{wire_net}' is shorted to ground via wire.")
                            break
    
    return shorts

