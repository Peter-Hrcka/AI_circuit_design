# core/schematic_to_circuit.py

from __future__ import annotations
from typing import Tuple, List, Dict, Optional

from .circuit import Circuit, Component
from .schematic_model import SchematicModel, SchematicComponent, SchematicPin


def _find_value(model: SchematicModel, ref: str) -> float:
    for comp in model.components:
        if comp.ref == ref:
            return float(comp.value)
    raise ValueError(f"Component {ref} not found in schematic model")


def circuit_from_non_inverting_schematic(model: SchematicModel) -> Circuit:
    """
    Build a Circuit from the current SchematicModel.

    Assumptions (for now):
      - There is exactly one Rin, R1, R2, and one U1 (OPAMP).
      - Rin, R1, R2 are 2-pin components.
      - OPAMP has pins: "+", "-", "OUT" whose .net fields define PLUS, MINUS, OUT nets.

    Nets are taken from SchematicPins (and merged by your wiring logic).
    We then canonicalize them to the node names your netlist builders expect.
    """
    # Find the components we care about
    comp_rin = comp_r1 = comp_r2 = comp_u1 = None

    for comp in model.components:
        if comp.ref == "Rin":
            comp_rin = comp
        elif comp.ref == "R1":
            comp_r1 = comp
        elif comp.ref == "R2":
            comp_r2 = comp
        elif comp.ref == "U1" and comp.ctype == "OPAMP":
            comp_u1 = comp

    missing = [name for name, c in [
        ("Rin", comp_rin),
        ("R1", comp_r1),
        ("R2", comp_r2),
        ("U1", comp_u1),
    ] if c is None]

    if missing:
        raise ValueError(f"Schematic is missing components: {', '.join(missing)}")

    # Get nets for resistors
    rin_n1, rin_n2 = _get_two_pin_nets(comp_rin)
    r1_n1, r1_n2 = _get_two_pin_nets(comp_r1)
    r2_n1, r2_n2 = _get_two_pin_nets(comp_r2)

    # Op-amp pins: find +, -, OUT
    plus_net = minus_net = out_net = None
    for p in comp_u1.pins:
        if p.name == "+":
            plus_net = _canon_net(p.net or "")
        elif p.name == "-":
            minus_net = _canon_net(p.net or "")
        elif p.name.upper() == "OUT":
            out_net = _canon_net(p.net or "")

    if not (plus_net and minus_net and out_net):
        raise ValueError("Op-amp U1 pins (+, -, OUT) must all have nets.")

    # Build Circuit
    circ = Circuit(name="Non-inverting (from schematic nets)")

    # Rin
    circ.components.append(
        Component(
            ref="Rin",
            ctype="R",
            node1=rin_n1,
            node2=rin_n2,
            value=float(comp_rin.value),
            unit="ohm",
        )
    )

    # R1
    circ.components.append(
        Component(
            ref="R1",
            ctype="R",
            node1=r1_n1,
            node2=r1_n2,
            value=float(comp_r1.value),
            unit="ohm",
        )
    )

    # R2
    circ.components.append(
        Component(
            ref="R2",
            ctype="R",
            node1=r2_n1,
            node2=r2_n2,
            value=float(comp_r2.value),
            unit="ohm",
        )
    )

    # Op-amp “logical” component: we only care about its nodes here
    circ.components.append(
        Component(
            ref="U1",
            ctype="OPAMP",
            node1=plus_net,
            node2=minus_net,
            value=0.0,
            unit="",
            extra={"gain": 1e6},
        )
    )

    return circ


def _canon_net(net: str) -> str:
    """
    Map schematic net labels to SPICE node names used by the rest of the code.

    Schematic uses: VIN, PLUS, MINUS, OUT, GND, N001, ...
    SPICE expects:  Vin, Vplus, Vminus, Vout, 0, others passed through.
    """
    if not net:
        return "NUNDEF"

    n = net.upper()

    if n == "VIN":
        return "Vin"
    if n == "PLUS":
        return "Vplus"
    if n == "MINUS":
        return "Vminus"
    if n in ("OUT", "VOUT"):
        return "Vout"
    if n in ("GND", "0"):
        return "0"

    # For auto nets like N001, N002, just keep them as-is
    return net

def _get_two_pin_nets(comp: SchematicComponent) -> Tuple[str, str]:
    """
    Return (net1, net2) for a 2-pin component. Raises if missing nets.
    """
    if len(comp.pins) != 2:
        raise ValueError(f"Component {comp.ref} is not 2-pin (has {len(comp.pins)} pins).")

    n1 = comp.pins[0].net
    n2 = comp.pins[1].net
    if n1 is None or n2 is None:
        raise ValueError(f"Component {comp.ref} has unassigned pin nets.")

    return _canon_net(n1), _canon_net(n2)


def circuit_from_schematic(model: SchematicModel) -> Circuit:
    """
    Convert any schematic into a generic Circuit.
    
    This is a general converter that works for any topology:
    - Each component contributes its type, value, and node connections
    - Nets from schematic pins become circuit nodes
    - Supports: R, C, V, OPAMP, GND, VOUT components
    
    Args:
        model: SchematicModel to convert
        
    Returns:
        Circuit object representing the schematic
    """
    circuit = Circuit(name="Circuit from schematic")
    
    # Track op-amps separately (they need special handling)
    opamps: List[SchematicComponent] = []
    
    # Track VOUT markers to identify output node
    vout_nodes: List[str] = []
    
    # Process all components
    for comp in model.components:
        if comp.ctype == "R":
            # Resistor: 2-pin component
            if len(comp.pins) != 2:
                raise ValueError(f"Resistor {comp.ref} must have 2 pins, has {len(comp.pins)}")
            n1, n2 = _get_two_pin_nets(comp)
            circuit.components.append(Component(
                ref=comp.ref,
                ctype="R",
                node1=n1,
                node2=n2,
                value=float(comp.value),
                unit="ohm",
            ))
        
        elif comp.ctype == "C":
            # Capacitor: 2-pin component
            if len(comp.pins) != 2:
                raise ValueError(f"Capacitor {comp.ref} must have 2 pins, has {len(comp.pins)}")
            n1, n2 = _get_two_pin_nets(comp)
            circuit.components.append(Component(
                ref=comp.ref,
                ctype="C",
                node1=n1,
                node2=n2,
                value=float(comp.value),
                unit="F",
            ))
        
        elif comp.ctype == "V":
            # Voltage source: 2-pin component (+ and -)
            if len(comp.pins) != 2:
                raise ValueError(f"Voltage source {comp.ref} must have 2 pins, has {len(comp.pins)}")
            n1, n2 = _get_two_pin_nets(comp)
            circuit.components.append(Component(
                ref=comp.ref,
                ctype="V",
                node1=n1,
                node2=n2,
                value=float(comp.value),
                unit="V",
            ))
        
        elif comp.ctype == "I":
            # Current source: 2-pin component (+ and -)
            if len(comp.pins) != 2:
                raise ValueError(f"Current source {comp.ref} must have 2 pins, has {len(comp.pins)}")
            n1, n2 = _get_two_pin_nets(comp)
            circuit.components.append(Component(
                ref=comp.ref,
                ctype="I",
                node1=n1,
                node2=n2,
                value=float(comp.value),
                unit="A",
            ))
        
        elif comp.ctype == "GND":
            # Ground: single pin, always connects to node "0"
            if len(comp.pins) != 1:
                raise ValueError(f"Ground {comp.ref} must have 1 pin, has {len(comp.pins)}")
            pin = comp.pins[0]
            if pin.net:
                # Connect the net to ground (node "0")
                # This is handled implicitly - the pin's net becomes "0"
                # We don't create a component for ground, but ensure the net is "0"
                pass  # Ground nets should already be "0" or "GND"
        
        elif comp.ctype == "VOUT":
            # VOUT marker: single pin that marks the output node
            if len(comp.pins) != 1:
                raise ValueError(f"VOUT marker {comp.ref} must have 1 pin, has {len(comp.pins)}")
            pin = comp.pins[0]
            if pin.net:
                # Store the net as an output node candidate
                vout_nodes.append(_canon_net(pin.net))
        
        elif comp.ctype == "OPAMP":
            # Op-amp: 3+ pins, needs special handling
            opamps.append(comp)
        
        else:
            # Unknown component type - skip with warning
            print(f"Warning: Unknown component type '{comp.ctype}' for {comp.ref}, skipping")
    
    # Handle op-amps (they have 3+ pins but Component only has node1/node2)
    # For op-amps, we use node1=non-inverting, node2=inverting, and store output in extra
    for opamp in opamps:
        plus_net = None
        minus_net = None
        out_net = None
        
        # Find pins by name
        for pin in opamp.pins:
            if pin.name == "+" or pin.name.upper() == "PLUS":
                plus_net = _canon_net(pin.net or "")
            elif pin.name == "-" or pin.name.upper() == "MINUS":
                minus_net = _canon_net(pin.net or "")
            elif pin.name.upper() == "OUT" or pin.name.upper() == "OUTPUT":
                out_net = _canon_net(pin.net or "")
        
        # If we couldn't find by name, use first 3 pins
        if not (plus_net and minus_net and out_net):
            if len(opamp.pins) >= 3:
                plus_net = _canon_net(opamp.pins[0].net or "")
                minus_net = _canon_net(opamp.pins[1].net or "")
                out_net = _canon_net(opamp.pins[2].net or "")
            else:
                raise ValueError(f"Op-amp {opamp.ref} needs at least 3 pins, has {len(opamp.pins)}")
        
        if not (plus_net and minus_net and out_net):
            raise ValueError(f"Op-amp {opamp.ref} pins must all have nets assigned")
        
        # Store output node and supply rails in extra dict
        extra = {"output_node": out_net, "gain": 1e6}  # Default gain
        
        # Transfer supply rails from schematic component if available
        if "vcc" in opamp.extra:
            extra["vcc"] = float(opamp.extra["vcc"])
        if "vee" in opamp.extra:
            extra["vee"] = float(opamp.extra["vee"])
        
        circuit.components.append(Component(
            ref=opamp.ref,
            ctype="OPAMP",
            node1=plus_net,   # non-inverting input
            node2=minus_net,  # inverting input
            value=0.0,
            unit="",
            extra=extra,
        ))
    
    # Store VOUT node information in circuit metadata for output detection
    if vout_nodes:
        # Use the first VOUT marker found (or could use all of them)
        circuit.metadata["output_node"] = vout_nodes[0]
        if len(vout_nodes) > 1:
            # If multiple VOUT markers, use the first one and warn
            print(f"Warning: Multiple VOUT markers found, using {vout_nodes[0]} as output node")
    
    return circuit
