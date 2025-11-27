from core.model_analyzer import analyze_model

meta = analyze_model(r"C:\Users\phrcka\Desktop\Playground\Apps\AI_circuit_designer\src\models\OP284.lib")

print(meta.short_summary())
print("Recommended simulator:", meta.recommended_simulator)
print("Vendor:", meta.vendor)
print("Models:", meta.model_names)
print("Conversion needed:", meta.conversion_needed)
print("Warnings:")
for w in meta.conversion_warnings:
    print("  -", w)



