"""
Netlist-related helpers.

For now:
- Provide a simple non-inverting op-amp circuit template.
- Later, this module will convert Circuit objects into full SPICE netlists.
"""

from __future__ import annotations
from typing import List, Dict

from .circuit import Circuit, Component
from .model_metadata import ModelMetadata  # add this import at the top


def _emit_opamp_block(lines: List[str], circuit: Circuit) -> None:
    """
    Append either a vendor op-amp instantiation or the internal OP284-like
    macromodel to 'lines', depending on what is stored in circuit.metadata.

    For vendor model:
        - emits .include "<file>"
        - adds simple supply rails
        - instantiates XU1 with a guessed pin order

    For built-in model:
        - emits EOPAMP_INT + RBUF + RPOLE + CPOLE
    """
    model_file = circuit.metadata.get("opamp_model_file")
    subckt_name = circuit.metadata.get("opamp_subckt_name")

    if model_file and subckt_name:
        # --- Vendor model path -----------------------------------------
        lines.append(f'.include "{model_file}"')
        lines.append("* Simple op-amp supply rails (adjust as needed)")
        lines.append("VCC VCC 0 DC 15")
        lines.append("VEE VEE 0 DC -15")

        # NOTE: You MUST match this pin order to the vendor model's .SUBCKT.
        # This is a very common order: +IN, -IN, OUT, VCC, VEE
        # If OP284 uses a different order, change this XU1 line accordingly.
        lines.append(
            f"XU1 Vplus Vminus Vout VCC VEE {subckt_name}"
        )
    else:
        # --- Built-in OP284-like macromodel (your existing behavior) ---
        lines.append("* OP284-like single-pole op-amp model")
        lines.append("* A0 = 2e5, GBW ~ 4 MHz -> fp ~ 20 Hz")
        lines.append("EOPAMP_INT NINT 0 Vplus Vminus 2e5")
        lines.append("RBUF NINT Vout 1")
        lines.append("RPOLE Vout 0 1k")
        lines.append("CPOLE Vout 0 7.9u")



def attach_vendor_opamp_model(
    circuit: Circuit,
    model_file: str,
    subckt_name: str,
    meta: ModelMetadata | None = None,
) -> None:
    """
    Attach a vendor op-amp model to this circuit.

    Args:
        circuit: Circuit to annotate.
        model_file: Path to the vendor .lib/.cir/.sub file (absolute or relative).
        subckt_name: Name of the .SUBCKT inside that file, e.g. "OP284".
        meta: Optional ModelMetadata from model_analyzer.analyze_model().
             If provided, we store the recommended simulator hint.

    This does NOT change any components. It only stores metadata so that
    the SPICE netlist builders know to emit .include + XU1 ... SUBCKT
    instead of the internal OP284-like macromodel.
    """
    circuit.metadata["opamp_model_file"] = model_file
    circuit.metadata["opamp_subckt_name"] = subckt_name

    if meta is not None:
        circuit.metadata["opamp_model_vendor"] = meta.vendor or ""
        circuit.metadata["opamp_model_rec_sim"] = meta.recommended_simulator
        circuit.metadata["opamp_model_is_pspice"] = str(meta.is_pspice)
        circuit.metadata["opamp_model_is_ltspice"] = str(meta.is_ltspice)






def non_inverting_opamp_template() -> Circuit:
    """
    Create a simple non-inverting op-amp stage:

        Vin ---[Rin]--> non-inverting input of op-amp
        Feedback network: R1 from Vout to inverting input
                          R2 from inverting input to ground

    Ideal gain: 1 + R1/R2

    For now, we assume an ideal op-amp block and ignore power pins, etc.
    """
    circuit = Circuit(name="Non-inverting opamp stage")

    # Input resistor (may be optional for AC gain, but it's nice to have it)
    circuit.add_component(Component(
        ref="Rin",
        ctype="R",
        node1="Vin",
        node2="Vplus",
        value=10_000.0,
        unit="ohm",
    ))

    # Feedback resistor R1: from Vout to Vminus
    circuit.add_component(Component(
        ref="R1",
        ctype="R",
        node1="Vout",
        node2="Vminus",
        value=90_000.0,   # 90k
        unit="ohm",
    ))

    # Resistor R2: from Vminus to ground
    circuit.add_component(Component(
        ref="R2",
        ctype="R",
        node1="Vminus",
        node2="0",
        value=10_000.0,   # 10k
        unit="ohm",
    ))

    # Ideal op-amp block (we don't use node3/value yet)
    circuit.add_component(Component(
        ref="U1",
        ctype="OPAMP",
        node1="Vplus",   # non-inverting input
        node2="Vminus",  # inverting input
        value=0.0,
        unit="",
        extra={"gain": 1e6},  # just a placeholder
    ))

    return circuit


def circuit_to_spice_netlist(circuit: Circuit) -> str:
    """
    Convert a Circuit object into a general SPICE netlist.
    
    Supports:
    - Resistors (R)
    - Capacitors (C)
    - Inductors (L)
    - Diodes (D)
    - BJTs (Q)
    - MOSFETs (M)
    - Voltage sources (V)
    - Current sources (I)
    - Voltage-controlled current sources (G)
    - Op-amps (OPAMP) - uses internal macromodel or vendor model
    """
    lines: List[str] = [f"* Netlist for circuit: {circuit.name}"]
    lines.append("")

    # Process components
    opamps: List[Component] = []
    diodes: List[Component] = []
    bjts: List[Component] = []
    mosfets: List[Component] = []
    
    for comp in circuit.components:
        if comp.ctype == "R":
            # RESISTOR: R<ref> node1 node2 value
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        
        elif comp.ctype == "C":
            # CAPACITOR: C<ref> node1 node2 value
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        
        elif comp.ctype == "L":
            # INDUCTOR: L<ref> node1 node2 value
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        
        elif comp.ctype == "D":
            # DIODE: D<ref> anode cathode model_name
            diodes.append(comp)
            model_name = comp.extra.get("model", "DDEFAULT")
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {model_name}")
        
        elif comp.ctype == "Q":
            # BJT: Q<ref> collector base emitter model_name
            bjts.append(comp)
            base_node = comp.extra.get("base_node", "")
            if not base_node:
                raise ValueError(f"BJT {comp.ref} missing base_node in extra dict")
            model_name = comp.extra.get("model")
            if not model_name:
                # Use default model based on polarity
                polarity = comp.extra.get("polarity", "NPN")
                model_name = "QNPN" if str(polarity).upper() == "NPN" else "QPNP"
            lines.append(f"{comp.ref} {comp.node1} {base_node} {comp.node2} {model_name}")
        
        elif comp.ctype == "M":
            # MOSFET: M<ref> drain gate source bulk model_name
            mosfets.append(comp)
            gate_node = comp.extra.get("gate_node", "")
            bulk_node = comp.extra.get("bulk_node", comp.node2)  # Default to source if not specified
            if not gate_node:
                raise ValueError(f"MOSFET {comp.ref} missing gate_node in extra dict")
            model_name = comp.extra.get("model")
            if not model_name:
                # Use default model based on type
                mos_type = comp.extra.get("mos_type", "NMOS")
                model_name = "NMOS_DEFAULT" if str(mos_type).upper() == "NMOS" else "PMOS_DEFAULT"
            lines.append(f"{comp.ref} {comp.node1} {gate_node} {comp.node2} {bulk_node} {model_name}")
        
        elif comp.ctype == "V":
            # VOLTAGE SOURCE: V<ref> node+ node- DC value
            # For AC analysis, we'll add AC 1 later if needed
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} DC {comp.value}")
        
        elif comp.ctype == "I":
            # CURRENT SOURCE: I<ref> node+ node- DC value
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} DC {comp.value}")
        
        elif comp.ctype == "G":
            # VOLTAGE-CONTROLLED CURRENT SOURCE: G<ref> np nn vp vn value
            ctrl_p = comp.extra.get("ctrl_p", "")
            ctrl_n = comp.extra.get("ctrl_n", "")
            if not ctrl_p or not ctrl_n:
                raise ValueError(f"VCCS {comp.ref} missing ctrl_p or ctrl_n in extra dict")
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {ctrl_p} {ctrl_n} {comp.value}")
        
        elif comp.ctype == "OPAMP":
            # Op-amp: store for special handling
            opamps.append(comp)
        
        elif comp.ctype in ("GND", "VOUT"):
            # Markers - no netlist lines needed
            pass
        
        else:
            # Unknown component type
            lines.append(f"* {comp.ref} type {comp.ctype} not yet implemented")
    
    # Handle op-amps (emit op-amp blocks)
    for opamp in opamps:
        _emit_general_opamp_block(lines, opamp, circuit.metadata)
    
    # Emit default models if needed (before .end)
    has_default_diode = any(d.extra.get("model") == "DDEFAULT" or "model" not in d.extra for d in diodes)
    has_default_bjt_npn = any(q.extra.get("model") == "QNPN" or (not q.extra.get("model") and str(q.extra.get("polarity", "NPN")).upper() == "NPN") for q in bjts)
    has_default_bjt_pnp = any(q.extra.get("model") == "QPNP" or (not q.extra.get("model") and str(q.extra.get("polarity", "PNP")).upper() == "PNP") for q in bjts)
    has_default_mos_nmos = any(m.extra.get("model") == "NMOS_DEFAULT" or (not m.extra.get("model") and str(m.extra.get("mos_type", "NMOS")).upper() == "NMOS") for m in mosfets)
    has_default_mos_pmos = any(m.extra.get("model") == "PMOS_DEFAULT" or (not m.extra.get("model") and str(m.extra.get("mos_type", "PMOS")).upper() == "PMOS") for m in mosfets)
    
    if has_default_diode:
        lines.append(".model DDEFAULT D(Is=1e-14 N=1)")
    if has_default_bjt_npn:
        lines.append(".model QNPN NPN (BF=100 IS=1e-14)")
    if has_default_bjt_pnp:
        lines.append(".model QPNP PNP (BF=100 IS=1e-14)")
    if has_default_mos_nmos:
        lines.append(".model NMOS_DEFAULT NMOS (LEVEL=1 VTO=1 KP=1e-3)")
    if has_default_mos_pmos:
        lines.append(".model PMOS_DEFAULT PMOS (LEVEL=1 VTO=-1 KP=5e-4)")
    
    lines.append("")
    lines.append(".end")
    return "\n".join(lines)


def _emit_general_opamp_block(lines: List[str], opamp: Component, metadata: Dict[str, str]) -> None:
    """
    Emit op-amp block for a general op-amp component.
    
    Uses node1=non-inverting, node2=inverting, and output_node from extra dict.
    Uses supply rails from opamp.extra if available, otherwise defaults.
    """
    plus_node = opamp.node1
    minus_node = opamp.node2
    out_node = opamp.extra.get("output_node", "Vout")
    
    # Get supply rails from component extra properties, or use defaults
    vcc = opamp.extra.get("vcc", 15.0)
    vee = opamp.extra.get("vee", -15.0)
    
    model_file = metadata.get("opamp_model_file")
    subckt_name = metadata.get("opamp_subckt_name")
    
    if model_file and subckt_name:
        # Vendor model path
        lines.append(f'.include "{model_file}"')
        lines.append(f"* Op-amp supply rails: VCC={vcc}V, VEE={vee}V")
        lines.append(f"VCC VCC 0 DC {vcc}")
        lines.append(f"VEE VEE 0 DC {vee}")
        # Common pin order: +IN, -IN, OUT, VCC, VEE
        lines.append(f"X{opamp.ref} {plus_node} {minus_node} {out_node} VCC VEE {subckt_name}")
    else:
        # Built-in OP284-like macromodel
        lines.append(f"* {opamp.ref}: OP284-like single-pole op-amp model")
        lines.append("* A0 = 2e5, GBW ~ 4 MHz -> fp ~ 20 Hz")
        nint = f"NINT_{opamp.ref}"
        lines.append(f"EOPAMP_INT_{opamp.ref} {nint} 0 {plus_node} {minus_node} 2e5")
        lines.append(f"RBUF_{opamp.ref} {nint} {out_node} 1")
        lines.append(f"RPOLE_{opamp.ref} {out_node} 0 1k")
        lines.append(f"CPOLE_{opamp.ref} {out_node} 0 7.9u")



def build_general_ac_netlist(
    circuit: Circuit,
    freq_hz: float = 1000.0,
    input_node: str = "Vin",
    output_node: str = "Vout",
    vsource_ref: str | None = None,
) -> str:
    """
    Build a general AC analysis netlist for any circuit topology.
    
    Args:
        circuit: Circuit to simulate
        freq_hz: Frequency for AC analysis
        input_node: Node name for input (will add AC source if not present)
        output_node: Node name for output (for measurement)
        vsource_ref: Optional reference of voltage source to use as AC input
    """
    lines: List[str] = [f"* AC analysis for circuit: {circuit.name}"]
    lines.append("")

    # Select which voltage source will be used as AC input
    selected_v = None
    for comp in circuit.components:
        if comp.ctype != "V":
            continue
        if vsource_ref and comp.ref == vsource_ref:
            selected_v = comp
            break
        if selected_v is None:
            selected_v = comp  # fallback to first V

    if selected_v is not None:
        has_vsource = True
        vsource_node = selected_v.node1  # assume positive terminal
        lines.append(
            f"{selected_v.ref} {selected_v.node1} {selected_v.node2} DC 0 AC 1"
        )
    else:
        has_vsource = False
        vsource_node = None

    # Add AC source if not present
    if not has_vsource:
        lines.append(f"V1 {input_node} 0 AC 1")
        vsource_node = input_node

    # Add all components
    opamps: List[Component] = []
    diodes: List[Component] = []
    bjts: List[Component] = []
    mosfets: List[Component] = []
    
    for comp in circuit.components:
        if comp.ctype == "R":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        elif comp.ctype == "C":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        elif comp.ctype == "L":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        elif comp.ctype == "D":
            diodes.append(comp)
            model_name = comp.extra.get("model", "DDEFAULT")
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {model_name}")
        elif comp.ctype == "Q":
            bjts.append(comp)
            base_node = comp.extra.get("base_node", "")
            model_name = comp.extra.get("model")
            if not model_name:
                polarity = comp.extra.get("polarity", "NPN")
                model_name = "QNPN" if str(polarity).upper() == "NPN" else "QPNP"
            lines.append(f"{comp.ref} {comp.node1} {base_node} {comp.node2} {model_name}")
        elif comp.ctype == "M":
            mosfets.append(comp)
            gate_node = comp.extra.get("gate_node", "")
            bulk_node = comp.extra.get("bulk_node", comp.node2)
            model_name = comp.extra.get("model")
            if not model_name:
                mos_type = comp.extra.get("mos_type", "NMOS")
                model_name = "NMOS_DEFAULT" if str(mos_type).upper() == "NMOS" else "PMOS_DEFAULT"
            lines.append(f"{comp.ref} {comp.node1} {gate_node} {comp.node2} {bulk_node} {model_name}")
        elif comp.ctype == "V":
            # Already handled above
            pass
        elif comp.ctype == "I":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} DC {comp.value}")
        elif comp.ctype == "G":
            ctrl_p = comp.extra.get("ctrl_p", "")
            ctrl_n = comp.extra.get("ctrl_n", "")
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {ctrl_p} {ctrl_n} {comp.value}")
        elif comp.ctype == "OPAMP":
            opamps.append(comp)
    
    # Emit op-amp blocks
    for opamp in opamps:
        _emit_general_opamp_block(lines, opamp, circuit.metadata)

    # Emit default models if needed
    has_default_diode = any(d.extra.get("model") == "DDEFAULT" or "model" not in d.extra for d in diodes)
    has_default_bjt_npn = any(q.extra.get("model") == "QNPN" or (not q.extra.get("model") and str(q.extra.get("polarity", "NPN")).upper() == "NPN") for q in bjts)
    has_default_bjt_pnp = any(q.extra.get("model") == "QPNP" or (not q.extra.get("model") and str(q.extra.get("polarity", "PNP")).upper() == "PNP") for q in bjts)
    has_default_mos_nmos = any(m.extra.get("model") == "NMOS_DEFAULT" or (not m.extra.get("model") and str(m.extra.get("mos_type", "NMOS")).upper() == "NMOS") for m in mosfets)
    has_default_mos_pmos = any(m.extra.get("model") == "PMOS_DEFAULT" or (not m.extra.get("model") and str(m.extra.get("mos_type", "PMOS")).upper() == "PMOS") for m in mosfets)
    
    if has_default_diode:
        lines.append(".model DDEFAULT D(Is=1e-14 N=1)")
    if has_default_bjt_npn:
        lines.append(".model QNPN NPN (BF=100 IS=1e-14)")
    if has_default_bjt_pnp:
        lines.append(".model QPNP PNP (BF=100 IS=1e-14)")
    if has_default_mos_nmos:
        lines.append(".model NMOS_DEFAULT NMOS (LEVEL=1 VTO=1 KP=1e-3)")
    if has_default_mos_pmos:
        lines.append(".model PMOS_DEFAULT PMOS (LEVEL=1 VTO=-1 KP=5e-4)")
    has_default_diode = any(d.extra.get("model") == "DDEFAULT" or "model" not in d.extra for d in diodes)
    has_default_bjt_npn = any(q.extra.get("model") == "QNPN" or (not q.extra.get("model") and str(q.extra.get("polarity", "NPN")).upper() == "NPN") for q in bjts)
    has_default_bjt_pnp = any(q.extra.get("model") == "QPNP" or (not q.extra.get("model") and str(q.extra.get("polarity", "PNP")).upper() == "PNP") for q in bjts)
    has_default_mos_nmos = any(m.extra.get("model") == "NMOS_DEFAULT" or (not m.extra.get("model") and str(m.extra.get("mos_type", "NMOS")).upper() == "NMOS") for m in mosfets)
    has_default_mos_pmos = any(m.extra.get("model") == "PMOS_DEFAULT" or (not m.extra.get("model") and str(m.extra.get("mos_type", "PMOS")).upper() == "PMOS") for m in mosfets)
    
    if has_default_diode:
        lines.append(".model DDEFAULT D(Is=1e-14 N=1)")
    if has_default_bjt_npn:
        lines.append(".model QNPN NPN (BF=100 IS=1e-14)")
    if has_default_bjt_pnp:
        lines.append(".model QPNP PNP (BF=100 IS=1e-14)")
    if has_default_mos_nmos:
        lines.append(".model NMOS_DEFAULT NMOS (LEVEL=1 VTO=1 KP=1e-3)")
    if has_default_mos_pmos:
        lines.append(".model PMOS_DEFAULT PMOS (LEVEL=1 VTO=-1 KP=5e-4)")

    # AC analysis
    lines.append("")
    lines.append(f".ac lin 1 {freq_hz} {freq_hz}")
    lines.append(f".print ac vm({output_node}) vm({vsource_node})")
    lines.append(".end")
    
    return "\n".join(lines)


def build_non_inverting_ac_netlist(
    circuit: Circuit,
    freq_hz: float = 1000.0,
) -> str:
    """
    Build AC netlist for non-inverting op-amp stage (backward compatibility).
    Now uses the general builder.
    """
    return build_general_ac_netlist(circuit, freq_hz=freq_hz, input_node="Vin", output_node="Vout")



def build_ac_sweep_netlist(
    circuit: Circuit,
    f_start: float = 10.0,
    f_stop: float = 1e7,
    points: int = 200,
    input_node: str = "Vin",
    output_node: str = "Vout",
    vsource_ref: str | None = None,
) -> str:
    """
    Build a general AC sweep netlist for any circuit topology.
    
    Args:
        circuit: Circuit to simulate
        f_start: Start frequency (Hz)
        f_stop: Stop frequency (Hz)
        points: Number of points
        input_node: Node name for input (will add AC source if not present)
        output_node: Node name for output (for measurement)
        vsource_ref: Optional reference of voltage source to use as AC input
    """
    lines = [f"* AC sweep for bandwidth - {circuit.name}"]
    lines.append("")

    # Select which voltage source will be used as AC input
    selected_v = None
    for comp in circuit.components:
        if comp.ctype != "V":
            continue
        if vsource_ref and comp.ref == vsource_ref:
            selected_v = comp
            break
        if selected_v is None:
            selected_v = comp  # fallback to first V

    if selected_v is not None:
        has_vsource = True
        vsource_node = selected_v.node1
        lines.append(
            f"{selected_v.ref} {selected_v.node1} {selected_v.node2} DC 0 AC 1"
        )
    else:
        has_vsource = False
        vsource_node = None
    
    if not has_vsource:
        lines.append(f"V1 {input_node} 0 AC 1")
        vsource_node = input_node

    # Add all components
    opamps: List[Component] = []
    diodes: List[Component] = []
    bjts: List[Component] = []
    mosfets: List[Component] = []
    
    for comp in circuit.components:
        if comp.ctype == "R":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        elif comp.ctype == "C":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        elif comp.ctype == "L":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        elif comp.ctype == "D":
            diodes.append(comp)
            model_name = comp.extra.get("model", "DDEFAULT")
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {model_name}")
        elif comp.ctype == "Q":
            bjts.append(comp)
            base_node = comp.extra.get("base_node", "")
            model_name = comp.extra.get("model")
            if not model_name:
                polarity = comp.extra.get("polarity", "NPN")
                model_name = "QNPN" if str(polarity).upper() == "NPN" else "QPNP"
            lines.append(f"{comp.ref} {comp.node1} {base_node} {comp.node2} {model_name}")
        elif comp.ctype == "M":
            mosfets.append(comp)
            gate_node = comp.extra.get("gate_node", "")
            bulk_node = comp.extra.get("bulk_node", comp.node2)
            model_name = comp.extra.get("model")
            if not model_name:
                mos_type = comp.extra.get("mos_type", "NMOS")
                model_name = "NMOS_DEFAULT" if str(mos_type).upper() == "NMOS" else "PMOS_DEFAULT"
            lines.append(f"{comp.ref} {comp.node1} {gate_node} {comp.node2} {bulk_node} {model_name}")
        elif comp.ctype == "V":
            # Already handled above
            pass
        elif comp.ctype == "I":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} DC {comp.value}")
        elif comp.ctype == "G":
            ctrl_p = comp.extra.get("ctrl_p", "")
            ctrl_n = comp.extra.get("ctrl_n", "")
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {ctrl_p} {ctrl_n} {comp.value}")
        elif comp.ctype == "OPAMP":
            opamps.append(comp)
    
    # Emit op-amp blocks
    for opamp in opamps:
        _emit_general_opamp_block(lines, opamp, circuit.metadata)

    # Emit default models if needed
    has_default_diode = any(d.extra.get("model") == "DDEFAULT" or "model" not in d.extra for d in diodes)
    has_default_bjt_npn = any(q.extra.get("model") == "QNPN" or (not q.extra.get("model") and str(q.extra.get("polarity", "NPN")).upper() == "NPN") for q in bjts)
    has_default_bjt_pnp = any(q.extra.get("model") == "QPNP" or (not q.extra.get("model") and str(q.extra.get("polarity", "PNP")).upper() == "PNP") for q in bjts)
    has_default_mos_nmos = any(m.extra.get("model") == "NMOS_DEFAULT" or (not m.extra.get("model") and str(m.extra.get("mos_type", "NMOS")).upper() == "NMOS") for m in mosfets)
    has_default_mos_pmos = any(m.extra.get("model") == "PMOS_DEFAULT" or (not m.extra.get("model") and str(m.extra.get("mos_type", "PMOS")).upper() == "PMOS") for m in mosfets)
    
    if has_default_diode:
        lines.append(".model DDEFAULT D(Is=1e-14 N=1)")
    if has_default_bjt_npn:
        lines.append(".model QNPN NPN (BF=100 IS=1e-14)")
    if has_default_bjt_pnp:
        lines.append(".model QPNP PNP (BF=100 IS=1e-14)")
    if has_default_mos_nmos:
        lines.append(".model NMOS_DEFAULT NMOS (LEVEL=1 VTO=1 KP=1e-3)")
    if has_default_mos_pmos:
        lines.append(".model PMOS_DEFAULT PMOS (LEVEL=1 VTO=-1 KP=5e-4)")

    lines.append("")
    lines.append(f".ac dec {points} {f_start} {f_stop}")
    lines.append(f".print ac vm({output_node}) vm({vsource_node})")
    lines.append(".end")

    return "\n".join(lines)


def build_dc_netlist(circuit: Circuit) -> str:
    """
    Build a DC analysis netlist for any circuit topology.
    
    Performs operating point analysis (.op) to get nodal voltages.
    
    Args:
        circuit: Circuit to simulate
        
    Returns:
        SPICE netlist string for DC analysis
    """
    lines: List[str] = [f"* DC analysis (operating point) for circuit: {circuit.name}"]
    lines.append("")
    
    # Check if there's already a voltage source or current source
    has_vsource = False
    has_isource = False
    for comp in circuit.components:
        if comp.ctype == "V":
            has_vsource = True
            # Use DC value from component
            dc_value = comp.value
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} DC {dc_value}")
            break
        elif comp.ctype == "I":
            has_isource = True
    
    # Add DC voltage source if not present and no current source (default 5V)
    # Note: If there's a current source, we don't need a default voltage source
    if not has_vsource and not has_isource:
        lines.append("V1 Vin 0 DC 5")
    
    # Add all components
    opamps: List[Component] = []
    diodes: List[Component] = []
    bjts: List[Component] = []
    mosfets: List[Component] = []
    
    for comp in circuit.components:
        if comp.ctype == "R":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        elif comp.ctype == "C":
            # For DC analysis, capacitors are open circuits
            # We can either omit them or add them with very large value
            # For now, we'll omit them (they don't affect DC)
            pass
        elif comp.ctype == "L":
            # For DC analysis, inductors are short circuits
            # We can either omit them or replace with small resistance
            # For now, we'll omit them (treat as short circuit)
            pass
        elif comp.ctype == "D":
            diodes.append(comp)
            model_name = comp.extra.get("model", "DDEFAULT")
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {model_name}")
        elif comp.ctype == "Q":
            bjts.append(comp)
            base_node = comp.extra.get("base_node", "")
            model_name = comp.extra.get("model")
            if not model_name:
                polarity = comp.extra.get("polarity", "NPN")
                model_name = "QNPN" if str(polarity).upper() == "NPN" else "QPNP"
            lines.append(f"{comp.ref} {comp.node1} {base_node} {comp.node2} {model_name}")
        elif comp.ctype == "M":
            mosfets.append(comp)
            gate_node = comp.extra.get("gate_node", "")
            bulk_node = comp.extra.get("bulk_node", comp.node2)
            model_name = comp.extra.get("model")
            if not model_name:
                mos_type = comp.extra.get("mos_type", "NMOS")
                model_name = "NMOS_DEFAULT" if str(mos_type).upper() == "NMOS" else "PMOS_DEFAULT"
            lines.append(f"{comp.ref} {comp.node1} {gate_node} {comp.node2} {bulk_node} {model_name}")
        elif comp.ctype == "V":
            # Already handled above
            pass
        elif comp.ctype == "I":
            # CURRENT SOURCE: I<ref> node+ node- DC value
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} DC {comp.value}")
        elif comp.ctype == "G":
            ctrl_p = comp.extra.get("ctrl_p", "")
            ctrl_n = comp.extra.get("ctrl_n", "")
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {ctrl_p} {ctrl_n} {comp.value}")
        elif comp.ctype == "OPAMP":
            opamps.append(comp)
    
    # Emit op-amp blocks
    for opamp in opamps:
        _emit_general_opamp_block(lines, opamp, circuit.metadata)
    
    # Emit default models if needed
    has_default_diode = any(d.extra.get("model") == "DDEFAULT" or "model" not in d.extra for d in diodes)
    has_default_bjt_npn = any(q.extra.get("model") == "QNPN" or (not q.extra.get("model") and str(q.extra.get("polarity", "NPN")).upper() == "NPN") for q in bjts)
    has_default_bjt_pnp = any(q.extra.get("model") == "QPNP" or (not q.extra.get("model") and str(q.extra.get("polarity", "PNP")).upper() == "PNP") for q in bjts)
    has_default_mos_nmos = any(m.extra.get("model") == "NMOS_DEFAULT" or (not m.extra.get("model") and str(m.extra.get("mos_type", "NMOS")).upper() == "NMOS") for m in mosfets)
    has_default_mos_pmos = any(m.extra.get("model") == "PMOS_DEFAULT" or (not m.extra.get("model") and str(m.extra.get("mos_type", "PMOS")).upper() == "PMOS") for m in mosfets)
    
    if has_default_diode:
        lines.append(".model DDEFAULT D(Is=1e-14 N=1)")
    if has_default_bjt_npn:
        lines.append(".model QNPN NPN (BF=100 IS=1e-14)")
    if has_default_bjt_pnp:
        lines.append(".model QPNP PNP (BF=100 IS=1e-14)")
    if has_default_mos_nmos:
        lines.append(".model NMOS_DEFAULT NMOS (LEVEL=1 VTO=1 KP=1e-3)")
    if has_default_mos_pmos:
        lines.append(".model PMOS_DEFAULT PMOS (LEVEL=1 VTO=-1 KP=5e-4)")
    
    # DC operating point analysis
    lines.append("")
    lines.append(".op")
    lines.append(".print dc")
    lines.append(".end")
    
    return "\n".join(lines)


def build_noise_netlist(
    circuit: Circuit,
    f_start: float = 10.0,
    f_stop: float = 20_000.0,
    points: int = 50,
    input_node: str = "Vin",
    output_node: str = "Vout",
    vsource_ref: str | None = None,
) -> str:
    """
    Build a general noise analysis netlist for any circuit topology.
    
    Args:
        circuit: Circuit to simulate
        f_start: Start frequency (Hz)
        f_stop: Stop frequency (Hz)
        points: Number of points
        input_node: Node name for input (will add AC source if not present)
        output_node: Node name for output (for measurement)
        vsource_ref: Optional reference of voltage source to use as AC input
    """
    lines = [f"* Noise analysis - {circuit.name}"]
    lines.append("")

    # Select which voltage source will be used as AC input
    selected_v = None
    for comp in circuit.components:
        if comp.ctype != "V":
            continue
        if vsource_ref and comp.ref == vsource_ref:
            selected_v = comp
            break
        if selected_v is None:
            selected_v = comp  # fallback to first V

    if selected_v is None:
        # If still no source, create a default AC source at the input node
        selected_v = Component(
            ref="V1",
            ctype="V",
            node1=input_node,
            node2="0",
            value=0.0,
            unit="V",
            extra={},
        )
        # Note: Add the source to the netlist
        lines.append(f"{selected_v.ref} {selected_v.node1} {selected_v.node2} DC 0 AC 1")
    else:
        # Input source MUST have DC and AC for .noise to be happy
        lines.append(
            f"{selected_v.ref} {selected_v.node1} {selected_v.node2} DC 0 AC 1"
        )

    vsource_name_for_noise = selected_v.ref

    # Add all components
    opamps: List[Component] = []
    diodes: List[Component] = []
    bjts: List[Component] = []
    mosfets: List[Component] = []
    
    for comp in circuit.components:
        if comp.ctype == "R":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        elif comp.ctype == "C":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        elif comp.ctype == "L":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        elif comp.ctype == "D":
            diodes.append(comp)
            model_name = comp.extra.get("model", "DDEFAULT")
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {model_name}")
        elif comp.ctype == "Q":
            bjts.append(comp)
            base_node = comp.extra.get("base_node", "")
            model_name = comp.extra.get("model")
            if not model_name:
                polarity = comp.extra.get("polarity", "NPN")
                model_name = "QNPN" if str(polarity).upper() == "NPN" else "QPNP"
            lines.append(f"{comp.ref} {comp.node1} {base_node} {comp.node2} {model_name}")
        elif comp.ctype == "M":
            mosfets.append(comp)
            gate_node = comp.extra.get("gate_node", "")
            bulk_node = comp.extra.get("bulk_node", comp.node2)
            model_name = comp.extra.get("model")
            if not model_name:
                mos_type = comp.extra.get("mos_type", "NMOS")
                model_name = "NMOS_DEFAULT" if str(mos_type).upper() == "NMOS" else "PMOS_DEFAULT"
            lines.append(f"{comp.ref} {comp.node1} {gate_node} {comp.node2} {bulk_node} {model_name}")
        elif comp.ctype == "V":
            # Already handled above
            pass
        elif comp.ctype == "I":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} DC {comp.value}")
        elif comp.ctype == "G":
            ctrl_p = comp.extra.get("ctrl_p", "")
            ctrl_n = comp.extra.get("ctrl_n", "")
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {ctrl_p} {ctrl_n} {comp.value}")
        elif comp.ctype == "OPAMP":
            opamps.append(comp)
    
    # Emit op-amp blocks
    for opamp in opamps:
        _emit_general_opamp_block(lines, opamp, circuit.metadata)

    # Emit default models if needed
    has_default_diode = any(d.extra.get("model") == "DDEFAULT" or "model" not in d.extra for d in diodes)
    has_default_bjt_npn = any(q.extra.get("model") == "QNPN" or (not q.extra.get("model") and str(q.extra.get("polarity", "NPN")).upper() == "NPN") for q in bjts)
    has_default_bjt_pnp = any(q.extra.get("model") == "QPNP" or (not q.extra.get("model") and str(q.extra.get("polarity", "PNP")).upper() == "PNP") for q in bjts)
    has_default_mos_nmos = any(m.extra.get("model") == "NMOS_DEFAULT" or (not m.extra.get("model") and str(m.extra.get("mos_type", "NMOS")).upper() == "NMOS") for m in mosfets)
    has_default_mos_pmos = any(m.extra.get("model") == "PMOS_DEFAULT" or (not m.extra.get("model") and str(m.extra.get("mos_type", "PMOS")).upper() == "PMOS") for m in mosfets)
    
    if has_default_diode:
        lines.append(".model DDEFAULT D(Is=1e-14 N=1)")
    if has_default_bjt_npn:
        lines.append(".model QNPN NPN (BF=100 IS=1e-14)")
    if has_default_bjt_pnp:
        lines.append(".model QPNP PNP (BF=100 IS=1e-14)")
    if has_default_mos_nmos:
        lines.append(".model NMOS_DEFAULT NMOS (LEVEL=1 VTO=1 KP=1e-3)")
    if has_default_mos_pmos:
        lines.append(".model PMOS_DEFAULT PMOS (LEVEL=1 VTO=-1 KP=5e-4)")

    # Use a control block so we can use ngspice 'noise' and 'print' commands
    lines.append("")
    lines.append(".control")
    lines.append(f"noise V({output_node}) {vsource_name_for_noise} dec {points} {f_start} {f_stop}")
    lines.append("setplot noise2")
    lines.append("print onoise_total inoise_total")
    lines.append("quit")
    lines.append(".endc")

    lines.append(".end")

    return '\n'.join(lines)



