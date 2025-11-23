"""
Netlist-related helpers.

For now:
- Provide a simple non-inverting op-amp circuit template.
- Later, this module will convert Circuit objects into full SPICE netlists.
"""

from __future__ import annotations
from typing import List

from .circuit import Circuit, Component






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
        # Ignore OPAMP component, we insert our own model.

        # 3) OP284-like single-pole op-amp model (ngspice-friendly)
    lines.append("* OP284-like single-pole op-amp model")
    lines.append("* A0 = 2e5, GBW ~ 4 MHz -> fp ~ 20 Hz")

    # Internal high-gain stage
    lines.append("EOPAMP_INT NINT 0 Vplus Vminus 2e5")

    # Small output resistor from internal node to output
    lines.append("RBUF NINT Vout 1")

    # RC at the output node sets dominant pole around 20 Hz:
    # fp = 1 / (2*pi*R*C) -> R = 1k, C ~= 7.9uF
    lines.append("RPOLE Vout 0 1k")
    lines.append("CPOLE Vout 0 7.9u")


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

    # OP284 model (same as before)
    lines.append("* OP284-like model")
    lines.append("EOPAMP_INT NINT 0 Vplus Vminus 2e5")
    lines.append("RBUF NINT Vout 1")
    lines.append("RPOLE Vout 0 1k")
    lines.append("CPOLE Vout 0 7.9u")

    lines.append(f".ac dec  {points}  {f_start}  {f_stop}")
    # lines.append(".print ac freq vm(Vout) vm(Vin)")
    lines.append(".print ac vm(Vout)")
    lines.append(".end")

    return "\n".join(lines)

def build_noise_netlist(
    circuit: Circuit,
    f_start: float = 10.0,
    f_stop: float = 20_000.0,
    points: int = 50,
) -> str:
    """
    Build a SPICE netlist for noise analysis of the non-inverting op-amp stage.

    We:
    - excite the circuit with source V1 (DC 0, AC 1)
    - reuse the OP284-like macromodel
    - run noise analysis over [f_start, f_stop] with 'dec' sweep
    - inside a .control block, print onoise_total and inoise_total
    """
    lines = [f"* Noise analysis - {circuit.name}"]

    # Input source MUST have DC and AC for .noise to be happy
    lines.append("V1 Vin 0 DC 0 AC 1")

    # Resistors
    for comp in circuit.components:
        if comp.ctype == "R":
            lines.append(f"{comp.ref} {comp.node1} {comp.node2} {comp.value}")

    # OP284 model (same as AC)
    lines.append("* OP284-like model")
    lines.append("EOPAMP_INT NINT 0 Vplus Vminus 2e5")
    lines.append("RBUF NINT Vout 1")
    lines.append("RPOLE Vout 0 1k")
    lines.append("CPOLE Vout 0 7.9u")

    # Use a control block so we can use ngspice 'noise' and 'print' commands
    lines.append(".control")
    lines.append(f"noise V(Vout) V1 dec {points} {f_start} {f_stop}")
    # Switch to the integrated-noise plot and print totals
    lines.append("setplot noise2")
    lines.append("print onoise_total inoise_total")
    lines.append("quit")
    lines.append(".endc")

    lines.append(".end")

    return '\n'.join(lines)


