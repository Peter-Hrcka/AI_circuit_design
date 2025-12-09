"""
Simple optimization routines.

MVP:
- A non-iterative "optimizer" for a non-inverting op-amp stage.
- It uses the formula: gain = 1 + R1/R2 (ideal case)
- For now, it ONLY adjusts R1 and keeps R2 fixed.

Later:
- This will be replaced/extended with:
  - SPICE-based evaluation
  - symbolic sensitivity analysis
  - multi-objective optimization
"""

from __future__ import annotations
from typing import Tuple

import math

from .circuit import Circuit, Component
from .netlist import build_non_inverting_ac_netlist, build_general_ac_netlist

from .spice_runner import SpiceError
from .simulator_manager import default_simulator_manager as sims
from .model_metadata import ModelMetadata







def _find_resistor(circuit: Circuit, ref: str) -> Component:
    comp = circuit.get_component(ref)
    if comp is None:
        raise ValueError(f"Resistor {ref} not found in circuit.")
    if comp.ctype != "R":
        raise ValueError(f"Component {ref} is not a resistor.")
    return comp


def compute_non_inverting_gain_db(circuit: Circuit) -> float:
    """
    Compute the ideal gain (in dB) of the non-inverting stage:

        Av = 1 + R1 / R2

    This is purely analytical and assumes an ideal op-amp.
    """
    r1 = _find_resistor(circuit, "R1")
    r2 = _find_resistor(circuit, "R2")

    av = 1.0 + r1.value / r2.value
    gain_db = 20.0 * math.log10(av)
    return gain_db


def optimize_gain_for_non_inverting_stage(
    circuit: Circuit,
    target_gain_db: float,
    max_iterations: int = 5,
) -> Tuple[Circuit, float]:
    """
    Very simple "optimizer" that adjusts R1 to hit target_gain_db.

    Steps:
    1) Read R2 from the circuit.
    2) Convert target_gain_db -> target linear gain.
    3) Compute the exact R1 that gives this gain for ideal op-amp.
    4) Apply a quantization to "nice" resistor values (optional, later).
    5) Return updated circuit + achieved gain.

    The `max_iterations` parameter is unused now, but we keep it
    because later we'll implement real iteration with SPICE in the loop.
    """
    optimized = Circuit(name=circuit.name)
    optimized.components = [Component(**vars(c)) for c in circuit.components]

    r1 = _find_resistor(optimized, "R1")
    r2 = _find_resistor(optimized, "R2")

    # 1) Convert target dB to linear gain
    target_linear = 10 ** (target_gain_db / 20.0)

    # 2) Solve for R1 from Av = 1 + R1 / R2  =>  R1 = (Av - 1) * R2
    new_r1_value = (target_linear - 1.0) * r2.value

    r1.value = new_r1_value

    # 3) Compute the achieved gain (with the exact value)
    achieved_gain_db = compute_non_inverting_gain_db(optimized)

    return optimized, achieved_gain_db

from typing import Optional, Tuple  # ensure Optional, Tuple imported

def optimize_gain_spice_loop(
    circuit: Circuit,
    target_gain_db: float,
    freq_hz: float = 1000.0,
    max_iterations: int = 5,
    tolerance_db: float = 0.1,
    model_meta: Optional[ModelMetadata] = None,
) -> Tuple[Circuit, float, int]:

    """
    SPICE-in-the-loop optimizer for the non-inverting op-amp stage.

    Strategy:
    1) Use the ideal math optimizer as a starting point (fast, exact for ideal op-amp).
    2) Then iterate:
       - build a SPICE netlist for AC analysis at freq_hz
       - run ngspice and measure the real gain (in dB)
       - compute error = target - measured
       - adjust R1 using a simple multiplicative rule
         (assumes gain is roughly proportional to R1, which is true enough
          for small corrections and a monotonic behavior)
    3) Stop when abs(error) < tolerance_db or max_iterations reached.

    Returns:
        (optimized_circuit, final_measured_gain_db, iterations_used)
    """
    # 1) Start from a copy of the circuit so we don't mutate the original.
    #    We reuse the same "cloning" strategy as the ideal optimizer.
    base_optimized, _ = optimize_gain_for_non_inverting_stage(
        circuit,
        target_gain_db=target_gain_db,
    )

    optimized = Circuit(name=base_optimized.name)
    optimized.components = [Component(**vars(c)) for c in base_optimized.components]

    def _get_r1() -> Component:
        return _find_resistor(optimized, "R1")

    # 2) Iterative SPICE-based tuning
    last_gain_db = None
    for it in range(1, max_iterations + 1):
        # Build netlist for current values (uses vendor or internal model)
        netlist = build_non_inverting_ac_netlist(optimized, freq_hz=freq_hz)

        try:
            res = sims.run_ac_gain(netlist, model_meta)
        except SpiceError as exc:
            # In a real app, you'd propagate this or log it.
            # For now, we break and return whatever we have.
            print(f"[SPICE ERROR in iteration {it}] {exc}")
            break

        measured_gain_db = res["gain_db"]
        last_gain_db = measured_gain_db

        error_db = target_gain_db - measured_gain_db
        print(
            f"[SPICE LOOP] iter={it}, measured={measured_gain_db:.2f} dB, "
            f"target={target_gain_db:.2f} dB, error={error_db:.2f} dB"
        )

        # Check convergence
        if abs(error_db) <= tolerance_db:
            print("[SPICE LOOP] Converged within tolerance.")
            return optimized, measured_gain_db, it

        # 3) Adjust R1.
        #
        # For a non-inverting stage, ideal gain is:
        #   Av_ideal = 1 + R1 / R2
        #
        # For small deviations and a reasonably high open-loop gain,
        # gain is approximately proportional to R1. So we use a simple
        # multiplicative update:
        #
        #   R1_new = R1_old * 10^(error_db / 20)
        #
        # Because:
        #   gain_db_new - gain_db_old ≈ 20 * log10(R1_new / R1_old)
        #
        r1 = _get_r1()
        factor = 10 ** (error_db / 20.0)
        new_r1 = r1.value * factor

        # Safety: avoid zero or negative values
        if new_r1 <= 0.0:
            new_r1 = max(r1.value * 0.1, 1.0)

        print(
            f"[SPICE LOOP] Updating R1: {r1.value:.3g} -> {new_r1:.3g} ohm "
            f"(factor {factor:.3f})"
        )
        r1.value = new_r1

    # If we exit the loop without converging, return the last measured value.
    if last_gain_db is None:
        # SPICE never ran successfully; fall back to ideal gain.
        last_gain_db = compute_non_inverting_gain_db(optimized)

    print("[SPICE LOOP] Reached max_iterations without meeting tolerance.")
    return optimized, last_gain_db, max_iterations

def _find_output_node(circuit: Circuit) -> str:
    """
    Find the output node for a circuit.
    Priority:
    1. VOUT marker from schematic (stored in circuit.metadata)
    2. Op-amp output nodes
    3. Nodes with "out" in the name
    4. Default to "Vout"
    """
    # First priority: Check for explicit VOUT marker
    vout_node = circuit.metadata.get("output_node")
    if vout_node:
        return vout_node
    
    # Second priority: Check for op-amp output nodes
    for comp in circuit.components:
        if comp.ctype == "OPAMP":
            out_node = comp.extra.get("output_node")
            if out_node:
                return out_node
    
    # Third priority: Look for nodes with "out" in the name (case-insensitive)
    all_nodes = set()
    for comp in circuit.components:
        all_nodes.add(comp.node1)
        all_nodes.add(comp.node2)
    
    for node in all_nodes:
        if "out" in node.lower():
            return node
    
    # Default
    return "Vout"


def measure_gain_spice(
    circuit,
    freq_hz: float,
    model_meta,
    input_node: str = "Vin",
    output_node: str | None = None,
    vsource_ref: str | None = None,
):
    """
    Run a single-frequency AC analysis for the given circuit and return
    the gain in dB at freq_hz, using the existing SimulatorManager.
    
    Args:
        circuit: Circuit to simulate
        freq_hz: Frequency for AC analysis
        model_meta: Model metadata (for simulator selection)
        input_node: Input node name (default: "Vin")
        output_node: Output node name (auto-detected if None)
        vsource_ref: Optional reference of voltage source to use as AC input
    """
    # Auto-detect output node if not provided
    if output_node is None:
        output_node = _find_output_node(circuit)
    
    # Build the AC netlist using general builder
    net = build_general_ac_netlist(
        circuit,
        freq_hz=freq_hz,
        input_node=input_node,
        output_node=output_node,
        vsource_ref=vsource_ref,
    )

    # Use the multi-backend manager (ngspice / Xyce) to run AC gain
    result = sims.run_ac_gain(net, model_meta)

    # Same structure as in your optimization / main pipeline
    return float(result["gain_db"])

