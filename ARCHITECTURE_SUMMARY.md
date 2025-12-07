# AI Circuit Designer - Architecture Summary

This document summarizes all modules in the repository according to the architectural layers defined in `.cursor/rules.md`.

## Architecture Overview

The system follows a 4-layer architecture:
- **Layer A**: Core Simulation Engine (`core/`)
- **Layer B**: Symbolic Engine (`core/symbolic/` - future)
- **Layer C**: AI Agent (`ai/`)
- **Layer D**: GUI Layer (`app/`)

---

## Layer A: Core Simulation Engine (`src/core/`)

### Circuit Representation
- **`circuit.py`**: Core data structures
  - `Component`: Represents circuit components (R, C, L, OPAMP) with ref, type, nodes, value, unit
  - `Circuit`: Container for components with metadata; provides methods to add/get components
  - **Status**: ✅ Implemented (MVP: 2-terminal components + op-amp blocks)

### Schematic Model
- **`schematic_model.py`**: GUI-level schematic representation
  - `SchematicPin`: Pin with name, coordinates, and net assignment
  - `SchematicComponent`: Component with ref, type, value, pins, position, rotation
  - `SchematicWire`: Wire segment with endpoints and net name
  - `SchematicModel`: Container for components and wires (source of truth for topology)
  - **Status**: ✅ Implemented (supports R, OPAMP components)

### Schematic ↔ Circuit Conversion
- **`schematic_to_circuit.py`**: Converts SchematicModel → Circuit
  - `circuit_from_non_inverting_schematic()`: Extracts component values and nets from schematic
  - Handles net name canonicalization (VIN→Vin, PLUS→Vplus, etc.)
  - **Status**: ✅ Implemented (non-inverting topology only)

- **`schematic_generate.py`**: Converts Circuit → SchematicModel
  - `non_inverting_circuit_to_schematic()`: Creates schematic model from optimized circuit
  - Generates pin positions and wire segments for visualization
  - **Status**: ✅ Implemented (non-inverting topology only)

### Netlist Generation
- **`netlist.py`**: SPICE netlist builders
  - `non_inverting_opamp_template()`: Creates initial circuit template
  - `attach_vendor_opamp_model()`: Attaches vendor model metadata to circuit
  - `build_non_inverting_ac_netlist()`: Single-frequency AC analysis netlist
  - `build_ac_sweep_netlist()`: Frequency sweep netlist for bandwidth
  - `build_noise_netlist()`: Noise analysis netlist
  - `_emit_opamp_block()`: Emits vendor subcircuit or internal macromodel
  - **Status**: ✅ Implemented (supports AC gain, AC sweep, noise analysis)

### SPICE Backends
- **`simulator_backend.py`**: Backend abstraction interface
  - `ISpiceBackend`: Abstract base class defining interface (run_ac_gain, run_ac_sweep, run_noise_sweep)
  - `NgSpiceBackend`: Concrete ngspice implementation
  - **Status**: ✅ Implemented (ngspice backend complete)

- **`spice_runner.py`**: Low-level ngspice execution (referenced but not read)
  - Wraps ngspice subprocess calls
  - Parses SPICE output
  - **Status**: ✅ Implemented (assumed from usage)

- **`xyce_backend.py`**: Xyce backend implementation (referenced but not read)
  - Concrete Xyce implementation of ISpiceBackend
  - **Status**: ✅ Implemented (assumed from usage in simulator_manager)

### Simulator Manager
- **`simulator_manager.py`**: Backend routing and selection
  - `SimulatorManager`: Registers backends (ngspice, Xyce) and routes models
  - `_choose_backend()`: Selects backend based on ModelMetadata
  - High-level convenience methods: `run_ac_gain()`, `run_ac_sweep()`, `run_noise_sweep()`
  - `default_simulator_manager`: Global instance
  - **Status**: ✅ Implemented (auto-selects backend based on model compatibility)

### Model Analysis & Compatibility
- **`model_analyzer.py`**: Vendor model compatibility detection
  - `analyze_model()`: Main entry point - scans SPICE model files
  - `_detect_features()`: Detects PSpice/LTspice constructs (A-devices, TABLE, behavioral functions)
  - `_guess_vendor()`: Identifies vendor from comments (TI, ADI, LTspice)
  - `_extract_model_names()`: Extracts .SUBCKT names
  - `_classify_from_flags()`: Maps features to ModelMetadata classification
  - **Status**: ✅ Implemented (detects standard SPICE, PSpice, LTspice, encrypted models)

- **`model_metadata.py`**: Model classification data structures
  - `ModelFeatureFlags`: Flags for detected features (A-devices, TABLE, PSpice/LTspice behaviors, encryption)
  - `ModelMetadata`: Classification result with vendor, compatibility flags, recommended simulator
  - **Status**: ✅ Implemented (complete metadata structure)

- **`model_conversion.py`**: Model simplification/conversion
  - `create_simple_opamp_model()`: Generates SPICE3-compatible single-pole macromodel
  - `maybe_convert_to_simple_opamp()`: Auto-converts non-standard models to simplified versions
  - Replaces complex vendor models with A0, GBW-based single-pole model
  - **Status**: ✅ Implemented (auto-conversion for PSpice/LTspice models)

### Analysis Helpers
- **`analysis.py`**: Post-simulation metric extraction
  - `find_3db_bandwidth()`: Extracts -3dB bandwidth from AC sweep results
  - `extract_gain_from_spice_output()`: Extracts gain from SPICE results
  - `summarize_noise()`: Summarizes noise analysis results
  - **Status**: ✅ Implemented (bandwidth, gain, noise metrics)

### Optimization
- **`optimization.py`**: Circuit optimization routines
  - `optimize_gain_for_non_inverting_stage()`: Ideal (symbolic) gain optimization using Av = 1 + R1/R2
  - `optimize_gain_spice_loop()`: SPICE-in-the-loop iterative optimization
  - `compute_non_inverting_gain_db()`: Calculates ideal gain from component values
  - `measure_gain_spice()`: Single SPICE gain measurement
  - **Status**: ✅ Implemented (hybrid symbolic + SPICE optimization for gain)

---

## Layer B: Symbolic Engine (`src/core/symbolic/`)

**Status**: ❌ Not yet implemented (future)

According to rules.md, this layer should contain:
- SymPy/Lcapy-based transfer function extraction
- Small-signal modeling (MNA - Modified Nodal Analysis)
- Sensitivity analysis (∂gain/∂R, ∂THD/∂C, etc.)
- Used by AI agent for smart parameter selection

**Current State**: Symbolic optimization is currently hardcoded in `optimization.py` (ideal gain formula). Full symbolic engine is planned for future.

---

## Layer C: AI Agent (`src/ai/`)

### Goal Parsing
- **`goals.py`**: Natural language goal parsing
  - `GainGoal`: Data structure for gain targets
  - `parse_goal()`: Rule-based parser (looks for "gain" + "dB" + number)
  - **Status**: ✅ Implemented (MVP: simple rule-based parsing)

### Agent Orchestration
- **`agent.py`**: AI orchestration layer
  - `apply_text_goal_to_circuit()`: Interprets user text goals and applies optimization
  - Currently calls `optimize_gain_for_non_inverting_stage()` based on parsed goals
  - **Status**: ✅ Implemented (MVP: rule-based, no LLM yet)

**Future Plans** (from rules.md):
- Parse natural language goals
- Decompose goals into optimization targets
- Choose between symbolic or brute-force optimizer
- Select component to modify
- Generate reasoning logs
- Suggest alternative topologies

---

## Layer D: GUI Layer (`src/app/`)

### Main Window
- **`gui_main.py`**: Main application window (PySide6/Qt)
  - `MainWindow`: Main application window class
  - Model file selector (browse for .lib files)
  - Target gain input, test frequency input
  - Run optimization button, re-simulate button, simulate from schematic button
  - Mode selector (Select/Edit vs Wire mode)
  - Tab widget: Log tab (text output) + Schematic tab
  - `on_run()`: Orchestrates full optimization workflow:
    1. Load & analyze vendor model
    2. Build initial circuit template
    3. Run ideal optimization
    4. Run SPICE-in-the-loop optimization
    5. Update schematic view
    6. Run AC sweep for bandwidth
    7. Run noise analysis
  - `on_component_clicked()`: Edit component values via dialog
  - `on_resimulate_current()`: Re-simulate without re-optimization
  - `on_simulate_from_schematic()`: Build circuit from schematic and simulate
  - **Status**: ✅ Implemented (complete workflow UI)

### Schematic View
- **`schematic_view.py`**: Schematic canvas (QGraphicsView/QGraphicsScene)
  - `SchematicView`: Interactive schematic editor
  - Select mode: Click components to edit values (emits `componentClicked` signal)
  - Wire mode: Click two pins to create wires, right-click to delete
  - `set_model()`: Updates schematic from SchematicModel
  - `_redraw_from_model()`: Renders components and wires from model
  - Supports R (resistors) and OPAMP components
  - Pin hit-testing, net merging, auto net name generation
  - Zoom (Ctrl+wheel), pan (drag)
  - **Status**: ✅ Implemented (basic schematic editor with wire mode)

### Entry Point
- **`main.py`**: Command-line entry point (alternative to GUI)
  - `load_opamp_model_with_conversion()`: Loads and converts vendor models
  - `main()`: Runs optimization workflow from command line
  - **Status**: ✅ Implemented (CLI alternative to GUI)

---

## Data Flow Summary

### Optimization Workflow (from rules.md):
1. Load vendor model → `model_analyzer.analyze_model()`
2. Analyze compatibility → `ModelMetadata` classification
3. If incompatible → `model_conversion.maybe_convert_to_simple_opamp()`
4. Build initial circuit → `netlist.non_inverting_opamp_template()`
5. Run symbolic design → `optimization.optimize_gain_for_non_inverting_stage()`
6. Run SPICE-in-the-loop → `optimization.optimize_gain_spice_loop()`
7. Construct schematic → `schematic_generate.non_inverting_circuit_to_schematic()`
8. Update GUI → `schematic_view.set_model()`

### Manual Schematic Editing Workflow:
1. User edits schematic → `SchematicModel` values update
2. Rebuild Circuit → `schematic_to_circuit.circuit_from_non_inverting_schematic()`
3. SPICE simulation → `simulator_manager.run_ac_gain()` / `run_ac_sweep()` / `run_noise_sweep()`
4. GUI updates → Results displayed in log

### AI Command Workflow:
1. User enters text goal → `ai.goals.parse_goal()`
2. AI selects strategy → `ai.agent.apply_text_goal_to_circuit()`
3. AI modifies circuit → Calls optimization routines
4. AI runs SPICE → Via simulator_manager
5. AI iterates → Until convergence
6. GUI updates → Schematic and log updated

---

## Module Status by Layer

### ✅ Fully Implemented
- **Layer A**: All core simulation modules (circuit, netlist, backends, model analysis, optimization)
- **Layer C**: Basic AI agent (rule-based goal parsing and optimization)
- **Layer D**: Complete GUI (main window, schematic view, workflows)

### ❌ Not Yet Implemented
- **Layer B**: Symbolic engine (SymPy/Lcapy, MNA, sensitivity analysis)
- **Layer C**: LLM integration (currently rule-based)
- **Layer D**: Advanced schematic features (component palette, drag-drop, rotate/flip, net labels)

### 🔄 Partially Implemented
- **Layer A**: Model conversion (basic single-pole model, needs enhancement for THD/transient accuracy)
- **Layer A**: Optimization (gain only, needs THD, noise, power, multi-objective)
- **Layer D**: Schematic editor (basic wire mode, needs full component placement UI)

---

## Architecture Compliance

The codebase **follows the architectural rules** defined in `.cursor/rules.md`:

✅ **Separation of Concerns**: GUI logic (`app/`) is separate from core logic (`core/`)
✅ **Schematic-Driven Topology**: `SchematicModel` is source of truth, conversion to `Circuit` is explicit
✅ **Backend Abstraction**: `ISpiceBackend` interface allows multiple simulators
✅ **Model Compatibility**: Automatic detection and routing to appropriate backend
✅ **Hybrid Optimization**: Symbolic (ideal) + SPICE-in-the-loop approach
✅ **Type Hints**: Full type annotations throughout
✅ **Modularity**: Small, focused modules with clear responsibilities

---

## Future Extensions (from rules.md)

Planned but not yet implemented:
- Transistor-level support (BJTs, MOSFETs)
- Cadence Virtuoso export
- Automatic topology generation
- Reinforcement-learning tuning
- Multi-objective optimization (Pareto fronts)
- Full schematic editor features (component palette, drag-drop, rotate/flip, wire snapping, group moving, copy/paste, net labeling)
- Symbolic engine (Layer B)


