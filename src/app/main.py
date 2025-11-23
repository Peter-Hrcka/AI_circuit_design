from __future__ import annotations

from core.netlist import non_inverting_opamp_template
from core.optimization import (
    optimize_gain_for_non_inverting_stage,
    optimize_gain_spice_loop,
)
from core.circuit import Circuit


def main() -> None:
    # 1) Start from template circuit
    circuit: Circuit = non_inverting_opamp_template()

    print("Initial circuit:")
    for comp in circuit.components:
        print(f"  {comp.ref}: {comp.ctype} {comp.value} {comp.unit} "
              f"{comp.node1} {comp.node2}")

    target_gain_db = 40.0
    freq_hz = 1000.0

    # 2) Ideal optimization (fast, purely analytical)
    ideal_circuit, ideal_gain_db = optimize_gain_for_non_inverting_stage(
        circuit,
        target_gain_db=target_gain_db,
    )

    print("\nAfter ideal (symbolic) optimization:")
    for comp in ideal_circuit.components:
        print(f"  {comp.ref}: {comp.ctype} {comp.value} {comp.unit} "
              f"{comp.node1} {comp.node2}")
    print(f"Target gain (ideal):   {target_gain_db:.2f} dB")
    print(f"Achieved (ideal):      {ideal_gain_db:.2f} dB")

    # 3) SPICE-in-the-loop refinement
    print("\nRunning SPICE-in-the-loop optimization...")
    final_circuit, measured_gain_db, it = optimize_gain_spice_loop(
        ideal_circuit,
        target_gain_db=target_gain_db,
        freq_hz=freq_hz,
        max_iterations=5,
        tolerance_db=0.1,
    )

    print("\nFinal circuit after SPICE-in-the-loop:")
    for comp in final_circuit.components:
        print(f"  {comp.ref}: {comp.ctype} {comp.value} {comp.unit} "
              f"{comp.node1} {comp.node2}")

    print(f"\nSPICE frequency:       {freq_hz:.1f} Hz")
    print(f"Target gain (SPICE):   {target_gain_db:.2f} dB")
    print(f"Achieved (SPICE):      {measured_gain_db:.2f} dB")
    print(f"Iterations used:       {it}")


if __name__ == "__main__":
    main()
