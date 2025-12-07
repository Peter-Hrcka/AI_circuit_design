# AI-Assisted Circuit Designer — Cursor Rules  
# (Updated according to full product plan)

This repository contains a next-generation **AI-assisted electronic circuit design system** that combines:

- SPICE simulation (ngspice + Xyce)
- Symbolic analysis (SymPy, Lcapy, MNA)
- AI reasoning (LLM-based agent)
- Automated optimization (gain, noise, THD, BW, power)
- GUI schematic editor (PySide6 / Qt)
- Vendor model integration (TI, ADI, LTspice libraries)
- Automated model compatibility & conversion
- Iterative design loop with AI decision-making

Cursor MUST always operate inside this context.

---

## 1. PRODUCT PURPOSE

The system is an intelligent circuit design assistant that can:

- Understand user design goals (e.g., “increase gain to 40 dB”, “reduce THD”, “minimize output noise”)
- Modify circuit parameters (values, topology)
- Generate SPICE netlists automatically
- Simulate circuits using:
  - **DC operating point**
  - **DC sweep**
  - **AC small-signal**
  - **Transient**
  - **Noise**
  - **FFT / THD** (via transient + post-processing)
  - **Parametric / temperature sweeps**
- Interpret results numerically and symbolically
- Suggest design adjustments
- Iterate until the design meets the user’s specs
- Allow the user to draw schematics LTspice-style
- Allow import of vendor SPICE models and choose correct backend automatically

Cursor must preserve this vision when editing code or adding features.

---

## 2. ARCHITECTURAL LAYERS (CURSOR MUST RESPECT)

### Layer A — Core Simulation Engine (`core/`)

- Circuit representation (`Circuit`, `Component`)
- Netlist generation for ngspice / Xyce
- SPICE backends:
  - **ngspice backend** (primary)
  - **Xyce backend** (fallback / PSpice support)
- Simulator manager (auto-select backend)
- Model analyzer + compatibility scoring
- Model conversion:
  - Detect unsupported constructs (A-devices, TABLE, limits, LTspice functions)
  - Replace with simplified SPICE3 macromodels (A0, GBW, poles)
- Analysis helpers (gain, bandwidth, noise, THD via FFT, etc.)

### 2.1 Supported Component Types (Core + GUI MUST support)

- **Passive components**
  - `R` – Resistor
  - `C` – Capacitor
  - `L` – Inductor

- **Diodes**
  - `D` – Standard diode, Zener, rectifier (model required)

- **Independent sources**
  - `V` – Voltage source (DC, AC, pulse, PWL)
  - `I` – Current source (DC, AC, pulse)

- **Controlled sources**
  - `G` – VCCS
  - `E` – VCVS
  - `F` – CCCS
  - `H` – CCVS

- **Op-amps**
  - `OPAMP` – Symbolic op-amp referencing vendor or simplified macromodel

- **Transistors**
  - `BJT` (`Q`) – NPN/PNP
  - `MOS` (`M`) – NMOS/PMOS, 4-terminal
  - Future: `JFET`, `SW`, behavioral `B`

Cursor must allow extension of these types without breaking existing logic.

### Layer B — Symbolic Engine (`core/symbolic/`)

- SymPy / Lcapy transfer function derivation
- MNA small-signal analysis
- Sensitivity analysis (∂gain/∂R, ∂THD/∂C, etc.)
- Used by AI agent for intelligent parameter selection

### Layer C — AI Agent (`ai/`)

- Parse natural language goals
- Decompose into analysis & optimization tasks
- Choose symbolic or brute-force optimization strategy
- Select components / topology to modify
- Generate reasoning logs
- Suggest alternative topologies

### Layer D — GUI Layer (`app/`)

- `MainWindow` (PySide6)
- Log panel
- Schematic editor (`SchematicView`)
- AI command panel (future)
- Model import UI
- Optimization workflow UI

Cursor MUST NOT mix GUI logic with core simulation logic.

## 3. SCHEMATIC EDITOR RULES (PySide6)

The schematic editor is LTspice-like:

- Components are rendered via QGraphicsView/QGraphicsItem
- Wires define **electrical nets**
- Nets propagate through pins → model.nets
- SchematicView provides:
  - Select mode
  - Wire mode
  - Component placement (future)
  - Drag/move components
  - Edit component parameters (dialog)
  - Snap-to-grid (future)
  - Visual net labels (future)
- SchematicModel is the **source of truth** for topology.
- Conversion to Circuit happens in `core/schematic_to_circuit.py`.

Cursor must maintain separation:

- GUI draws
- Model stores electrical meaning
- Core layer generates Circuit and SPICE netlists

---

## 4. SIMULATION WORKFLOW RULES

### 4.1 SPICE Analyses (MUST be supported)

- **DC Operating Point (`.op`)**
  - Bias verification, starting condition for other analyses

- **DC Sweep (`.dc`)**
  - Parameter / voltage sweeps

- **AC Small-Signal (`.ac`)**
  - Gain, phase, bandwidth, stability, open-loop analysis

- **Transient (`.tran`)**
  - Time-domain behavior, slew rate, ringing, distortion

- **Noise (`.noise`)**
  - Input/output-referred spectral noise, integrated noise

- **FFT / THD**
  - Derived from transient via `.four`, `.fft`, or Python FFT

- **Parametric Sweeps (`.step`)**
  - Value sweeps, load sweeps, bias sweeps

- **Temperature sweep (`.temp`)**

- **Monte Carlo (future)**

Cursor must NOT remove any of these capabilities.

---

## 5. MODEL COMPATIBILITY ENGINE

### 5.1 Detection Rules

Model analyzer MUST classify models as:

- Standard SPICE (SPICE3)
- PSpice-like (A-devices, TABLE, unsupported expressions)
- LTspice-specific
- Encrypted / compiled

### 5.2 Backend Routing Rules

- Standard → **ngspice**
- PSpice-like → **Xyce**
- LTspice-only → try **Xyce**, else simplify
- Encrypted → warn user, fallback to macromodel

### 5.3 Model Conversion Rules

- Preserve op-amp A0, GBW
- Provide dominant pole for AC & noise compatibility
- Ignore complex dynamic features (slew rate, clipping) in MVP
- Output SPICE3-safe `.subckt`

Cursor must NOT break or bypass this layer.

---

## 6. OPTIMIZATION RULES

Cursor MUST preserve:

- Two-stage optimization:
  1. Symbolic (predict near-optimal values)
  2. Numerical SPICE refinement

Supported optimization goals:

- Gain
- Bandwidth
- Noise
- THD (FFT-based)
- Power consumption (future)
- Phase margin / loop stability (future)
- Multi-objective optimization (future)

Optimizers MUST use clean data flow:

`Circuit → netlist → SPICE → metrics → reasoning → update Circuit`

---

## 7. CODING STYLE RULES FOR CURSOR

- Python 3.10+
- Full type hints
- Use dataclasses (`Circuit`, `Component`, `SchematicComponent`, etc.)
- Keep GUI out of `core/`
- Avoid circular imports
- No hidden global state
- Use deterministic pure functions except when interacting with AI
- Maintain compatibility with ngspice & Xyce
- Clear docstring at top of each new module

---

## 8. FUTURE EXTENSION RULES

Cursor must ensure compatibility with these future features:

- Full transistor-level support (BJT, MOSFET, JFET)
- Component palette and drag-drop UI
- Automatic topology generation
- Reinforcement-learning tuning
- Multi-objective Pareto search
- Schematic snapping, rotate/flip, net labels
- Multi-page and hierarchical schematics
- Cadence Virtuoso export (netlist-level)

---

## 9. MUST-NOT RULES (Very Important)

Cursor must **not**:

- Break the optimization loop
- Remove SPICE backend autodetection
- Hardcode file paths
- Mix GUI with core simulation logic
- Bypass model compatibility checks
- Break expected schematic behavior:
  - Drag from component = MOVE  
  - Drag from empty space = SELECT
- Introduce code preventing future EDA integration

---

## 10. EXPECTED BEHAVIOR FROM CURSOR

Cursor must:

- Respect architecture boundaries
- Maintain modularity & clarity
- Keep schematic-driven simulation correct
- Preserve simulation accuracy
- Keep code clean, extensible, production-ready
- Move the project toward a full LTspice-class + AI-driven design platform

End of rules.
