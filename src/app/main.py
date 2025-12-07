from __future__ import annotations

from core.netlist import (
    non_inverting_opamp_template,
    build_non_inverting_ac_netlist,
    build_ac_sweep_netlist,
    build_noise_netlist,
    attach_vendor_opamp_model,
)
from core.model_analyzer import analyze_model
from core.model_conversion import maybe_convert_to_simple_opamp
from core.simulator_manager import default_simulator_manager as sims
from core.model_metadata import ModelMetadata
from core.optimization import (
    optimize_gain_for_non_inverting_stage,
    optimize_gain_spice_loop,
)
from core.analysis import find_3db_bandwidth


def load_opamp_model_with_conversion(path: str) -> ModelMetadata:
    """
    Analyze a vendor op-amp model, and if it is non-standard (PSpice/LTspice-like),
    auto-generate a simplified single-pole macromodel for SPICE3.

    Returns ModelMetadata for the model that SHOULD be used in simulations:
    - either the original (standard SPICE) or
    - a simplified _simple.lib replacement.
    """
    meta_orig = analyze_model(path)
    print("Model analysis:")
    print("  Summary:", meta_orig.short_summary())
    print("  Recommended simulator:", meta_orig.recommended_simulator)
    print("  Vendor:", meta_orig.vendor)
    print("  Models:", meta_orig.model_names)
    print()

    meta_conv = maybe_convert_to_simple_opamp(meta_orig, auto_for_nonstandard=True)

    if meta_conv.path != meta_orig.path:
        print("Model conversion:")
        print(f"  Original file:   {meta_orig.path}")
        print(f"  Simplified file: {meta_conv.path}")
        for w in meta_conv.conversion_warnings:
            print("  Warning:", w)
        print()
    else:
        print("No conversion applied (model is standard SPICE or auto-conversion disabled).")
        print()

    return meta_conv


def main() -> None:
    # 0) Create initial circuit template
    circuit = non_inverting_opamp_template()

    print("Initial circuit:")
    for comp in circuit.components:
        print(
            f"  {comp.ref}: {comp.ctype} {comp.value} {comp.unit} "
            f"{comp.node1} {comp.node2}"
        )

    # 1) Load / analyze / maybe convert vendor model (e.g. OP284.lib)
    #    TODO: later this path will come from GUI / user settings.
    opamp_model_path = r"C:\Users\phrcka\Desktop\Playground\Apps\AI_circuit_designer\src\models\OP284.lib"  # adjust to your real path

    meta_model = load_opamp_model_with_conversion(opamp_model_path)

    # Choose subckt name to use when instantiating:
    # - Prefer first .SUBCKT name from the metadata
    if meta_model.model_names:
        subckt_name = meta_model.model_names[0]
    else:
        # Fallback to filename stem if analyzer didn't find .SUBCKT (unlikely)
        from pathlib import Path
        subckt_name = Path(meta_model.path).stem

    # Attach the (possibly simplified) model to the circuit
    attach_vendor_opamp_model(
        circuit,
        model_file=meta_model.path,
        subckt_name=subckt_name,
        meta=meta_model,
    )

    # 2) Ideal symbolic optimization (unchanged)
    target_gain_db = 40.0
    ideal_circuit, ideal_gain_db = optimize_gain_for_non_inverting_stage(
        circuit,
        target_gain_db=target_gain_db,
    )

    print("\nAfter ideal (symbolic) optimization:")
    for comp in ideal_circuit.components:
        print(
            f"  {comp.ref}: {comp.ctype} {comp.value} {comp.unit} "
            f"{comp.node1} {comp.node2}"
        )
    print(f"Target gain (ideal):   {target_gain_db:.2f} dB")
    print(f"Achieved (ideal):      {ideal_gain_db:.2f} dB")

    # 3) SPICE-in-the-loop optimization (we’ll adjust this in Step 2 below)
    print("\nRunning SPICE-in-the-loop optimization...")
    final_circuit, measured_gain_db, iters = optimize_gain_spice_loop(
        ideal_circuit,
        target_gain_db=target_gain_db,
        freq_hz=1_000_000.0,   # or 1e3, 1e6, etc.
        max_iterations=5,
        tolerance_db=0.1,
        model_meta=meta_model,  # <-- new parameter we’ll add to optimization.py
    )

    print("\nFinal circuit after SPICE-in-the-loop:")
    for comp in final_circuit.components:
        print(
            f"  {comp.ref}: {comp.ctype} {comp.value} {comp.unit} "
            f"{comp.node1} {comp.node2}"
        )

    print(f"\nSPICE frequency:       {1_000_000.0:.1f} Hz")
    print(f"Target gain (SPICE):   {target_gain_db:.2f} dB")
    print(f"Achieved (SPICE):      {measured_gain_db:.2f} dB")
    print(f"Iterations used:       {iters}")

    # 4) Bandwidth (AC sweep) using the same (final) circuit and same model
    print("\nRunning AC sweep for bandwidth...")
    ac_net = build_ac_sweep_netlist(final_circuit)
    ac_res = sims.run_ac_sweep(ac_net, meta_model)
    bw = find_3db_bandwidth(ac_res["freq_hz"], ac_res["gain_db"])
    if bw is None:
        print("Bandwidth (-3 dB): > sweep range (no rolloff found)")
    else:
        print(f"Bandwidth (-3 dB): {bw/1000:.2f} kHz")

    # 5) Noise (10 Hz – 20 kHz) using same circuit & model
    print("\nRunning noise analysis (10 Hz – 20 kHz).")
    noise_net = build_noise_netlist(final_circuit)
    noise_res = sims.run_noise_sweep(noise_net, meta_model)

    onoise = noise_res["total_onoise_rms"]
    inoise = noise_res["total_inoise_rms"]
    print(f"Total output noise 10 Hz–20 kHz: {onoise*1e6:.2f} µV_rms")
    print(f"Equivalent input noise 10 Hz–20 kHz: {inoise*1e9:.2f} nV_rms")


if __name__ == "__main__":
    main()
