"""
Netlist-related helpers.

For now:
- Provide a simple non-inverting op-amp circuit template.
- Later, this module will convert Circuit objects into full SPICE netlists.
"""

from __future__ import annotations
from typing import List

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
    Convert a Circuit object into a VERY SIMPLE SPICE-like netlist.

    This is a placeholder that we will later extend:
    - proper op-amp subcircuits
    - analysis commands
    - .include lines for vendor models
    """
    lines: List[str] = [f"* Netlist for circuit: {circuit.name}"]

    for comp in circuit.components:
        if comp.ctype == "R":
            # RESISTOR: R<ref> node1 node2 value
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")
        elif comp.ctype == "OPAMP":
            # Placeholder: an ideal op-amp notation (to be replaced with real subckt)
            lines.append(
                f"* {comp.ref} OPAMP between {comp.node1} and {comp.node2} (ideal placeholder)"
            )
        else:
            # Unknown / not yet implemented
            lines.append(
                f"* {comp.ref} type {comp.ctype} not yet implemented in netlist exporter"
            )

    lines.append(".end")
    return "\n".join(lines)



def build_non_inverting_ac_netlist(
    circuit: Circuit,
    freq_hz: float = 1000.0,
) -> str:
    lines: List[str] = [f"* AC gain test for circuit: {circuit.name}"]

    # 1) AC source: 1V from Vin to ground
    lines.append("V1 Vin 0 AC 1")

    # 2) Resistors from the circuit
    for comp in circuit.components:
        if comp.ctype == "R":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")

    # 3) Op-amp: either vendor model (if attached) or internal macro
    _emit_opamp_block(lines, circuit)

    # 4) AC analysis at a single frequency
    lines.append(f".ac lin 1 {freq_hz} {freq_hz}")

    # 5) Print magnitudes of Vout and Vin
    lines.append(".print ac vm(Vout) vm(Vin)")

    lines.append(".end")
    return "\n".join(lines)



def build_ac_sweep_netlist(
    circuit: Circuit,
    f_start: float = 10.0,
    f_stop: float = 1e7,
    points: int = 200,
) -> str:

    lines = [f"* AC sweep for bandwidth - {circuit.name}"]
    lines.append("V1 Vin 0 AC 1")

    for comp in circuit.components:
        if comp.ctype == "R":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")

    # Op-amp block (vendor or built-in)
    _emit_opamp_block(lines, circuit)

    lines.append(f".ac dec  {points}  {f_start}  {f_stop}")
    # Safer for both ngspice & Xyce: print both Vout and Vin
    lines.append(".print ac vm(Vout) vm(Vin)")
    lines.append(".end")

    return "\n".join(lines)


def build_noise_netlist(
    circuit: Circuit,
    f_start: float = 10.0,
    f_stop: float = 20_000.0,
    points: int = 50,
) -> str:
    lines = [f"* Noise analysis - {circuit.name}"]

    # Input source MUST have DC and AC for .noise to be happy
    lines.append("V1 Vin 0 DC 0 AC 1")

    # Resistors
    for comp in circuit.components:
        if comp.ctype == "R":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")

    # Op-amp block (vendor or built-in)
    _emit_opamp_block(lines, circuit)

    # Use a control block so we can use ngspice 'noise' and 'print' commands
    lines.append(".control")
    lines.append(f"noise V(Vout) V1 dec {points} {f_start} {f_stop}")
    lines.append("setplot noise2")
    lines.append("print onoise_total inoise_total")
    lines.append("quit")
    lines.append(".endc")

    lines.append(".end")

    return '\n'.join(lines)



