from core.model_analyzer import analyze_model
from core.simulator_manager import default_simulator_manager as sims
from core.netlist import non_inverting_opamp_template, build_non_inverting_ac_netlist

# 1) Analyze vendor model file
meta = analyze_model(r"C:\Users\phrcka\Desktop\Playground\Apps\AI_circuit_designer\src\models\OP284.lib")  # <- put real path here
print("Model analysis:")
print("  Summary:", meta.short_summary())
print("  Recommended simulator:", meta.recommended_simulator)
print("  Vendor:", meta.vendor)
print("  Models:", meta.model_names)
print()

# 2) Build a simple non-inverting op-amp circuit
circuit = non_inverting_opamp_template()

# 3) Build AC netlist at 1 kHz
net = build_non_inverting_ac_netlist(circuit, freq_hz=1000.0)

print("Generated netlist:")
print(net)
print()

# 4) Run AC gain through SimulatorManager (should route to Xyce if meta prefers Xyce)
print("Running AC gain via SimulatorManager...")
res = sims.run_ac_gain(net, meta)

print("Simulation result:")
for k, v in res.items():
    print(f"  {k}: {v}")
