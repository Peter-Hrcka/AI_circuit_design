from __future__ import annotations

from core.netlist import non_inverting_opamp_template
from core.optimization import (
    optimize_gain_for_non_inverting_stage,
    optimize_gain_spice_loop,
)
from core.circuit import Circuit

from core.netlist import build_ac_sweep_netlist
from core.spice_runner import run_spice_ac_sweep
from core.analysis import find_3db_bandwidth

from core.netlist import build_noise_netlist
from core.spice_runner import run_spice_noise_sweep
from core.analysis import summarize_noise


def main() -> None:
    # 1) Start from template circuit
    circuit: Circuit = non_inverting_opamp_template()

    print("Initial circuit:")
    for comp in circuit.components:
        print(f"  {comp.ref}: {comp.ctype} {comp.value} {comp.unit} "
              f"{comp.node1} {comp.node2}")

    target_gain_db = 40.0
    freq_hz = 1000000.0

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

    print("\nRunning AC sweep for bandwidth...")
    net = build_ac_sweep_netlist(final_circuit)
    ac = run_spice_ac_sweep(net)

    bw = find_3db_bandwidth(ac["freq_hz"], ac["gain_db"])
    if bw is None:
        print("Bandwidth (-3 dB): > sweep range (no rolloff found)")
    else:
        print(f"Bandwidth (-3 dB): {bw/1000:.2f} kHz")

    # 4) Noise analysis 10 Hz – 20 kHz
    print("\nRunning noise analysis (10 Hz – 20 kHz).")
    noise_net = build_noise_netlist(
        final_circuit,
        f_start=10.0,
        f_stop=20_000.0,
        points=50,
    )
    noise_res = run_spice_noise_sweep(noise_net)

    onoise = noise_res["total_onoise_rms"]   # V_rms at output
    inoise = noise_res["total_inoise_rms"]   # V_rms equivalent at input

    print(f"Total output noise 10 Hz–20 kHz: {onoise*1e6:.2f} µV_rms")
    print(f"Equivalent input noise 10 Hz–20 kHz: {inoise*1e9:.2f} nV_rms")




if __name__ == "__main__":
    main()
