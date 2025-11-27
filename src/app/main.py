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

from core.netlist import non_inverting_opamp_template, attach_vendor_opamp_model, build_non_inverting_ac_netlist
from core.model_analyzer import analyze_model
from core.simulator_manager import default_simulator_manager as sims


def main():
    circuit = non_inverting_opamp_template()

    # 1) Analyze the vendor model
    model_path = r"C:\Users\phrcka\Desktop\Playground\Apps\AI_circuit_designer\src\models\OP284.lib"
    meta = analyze_model(model_path)
    print(meta.short_summary())
    print("Recommended simulator:", meta.recommended_simulator)

    # 2) Attach the vendor op-amp model to the circuit
    #    We assume the .SUBCKT name is "OP284"
    attach_vendor_opamp_model(circuit, model_path, subckt_name="OP284", meta=meta)

    # 3) Build a netlist that uses the vendor model (not the internal macro)
    netlist = build_non_inverting_ac_netlist(circuit, freq_hz=1e3)
    print("\nGenerated netlist:\n", netlist)

    # 4) Run via SimulatorManager (ngspice or Xyce chosen automatically)
    res = sims.run_ac_gain(netlist, meta)
    print("\nMeasured gain:", res["gain_db"], "dB")




if __name__ == "__main__":
    main()
