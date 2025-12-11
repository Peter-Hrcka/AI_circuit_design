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

#### 2.2 GUI Layout Structure (Cursor MUST follow this high-level layout)

The main GUI should follow a professional EDA / CAD layout with dockable panels:

- **Top: Menu Bar**
  - Menus: `File`, `Edit`, `View`, `Simulation`, `AI`, `Tools`, `Help`.

- **Below Menu: Main Toolbar**
  - Editing tools (left side):
    - Pointer / Select
    - Wire tool
    - Quick placement for: Resistor (R), Capacitor (C), Inductor (L), Diode (D)
    - Quick placement for: BJT, MOSFET, Op-amp
    - Quick placement for: Voltage Source, Current Source, Ground
    - Net label tool
    - Rotate, Flip, Delete
  - Simulation & AI controls (right side):
    - Run DC, Run AC, Run Transient, Run Noise, Run FFT/THD
    - “AI Optimize” and “AI Explain Circuit” actions

- **Central Area: Schematic Editor (QGraphicsView)**
  - Displays the schematic:
    - Grid background (optional)
    - Components as schematic symbols
    - Wires as connections with junction dots
    - Net labels (VIN, VOUT, GND, N001, etc.)
  - Interaction:
    - Pan & zoom
    - Click to select
    - Drag component to move
    - Drag from empty space to draw selection rectangle
    - Wires snap to pins

- **Left Dock: Component Library**
  - Dockable `QDockWidget` with a component palette:
    - Categories:
      - Passive: R, C, L
      - Semiconductors: Diode, Zener, BJT (NPN/PNP), MOSFET (NMOS/PMOS)
      - Sources: Voltage (DC/AC/Pulse/PWL), Current (DC/AC)
      - Controlled sources: VCVS (E), VCCS (G), CCCS (F), CCVS (H)
      - Op-amps: Generic + vendor op-amps
      - User Macros (future subcircuits)
    - Components can be selected or drag-dropped into the schematic.

- **Right Dock: Properties / Inspector Panel**
  - Shows context-sensitive properties of the currently selected object:
    - For components:
      - Reference (R1, C3, Q2, M1, etc.)
      - Value (e.g. 10kΩ, 100nF, 1mH)
      - Model (e.g. 2N3904, OP27)
      - Orientation (rotation, flip)
      - Pins and net names for each pin
      - Extra parameters (for BJTs/MOSFETs, controlled sources, etc.)
    - For nets/wires:
      - Net name
      - Connected pins/components
    - For empty selection:
      - Global schematic + simulation defaults.

- **Bottom Dock: Log & Results Panel**
  - Dockable `QDockWidget` with tabbed views:
    - Simulation Log (ngspice / Xyce output)
    - AI Log (agent reasoning and suggestions)
    - Netlist Preview (generated SPICE netlist)
    - Plots (Bode / AC plot, Noise plot, Transient waveforms, FFT/THD)
  - Plots may be embedded (matplotlib/pyqtgraph) as dockable widgets.

Cursor MUST treat this layout as the target structure when adding or modifying GUI elements:
- Use QDockWidget for side and bottom panels.
- Use QGraphicsView for the schematic canvas.
- Keep toolbar/buttons and panels aligned with these responsibilities.


### 2.3 Keyboard Shortcuts (Cursor MUST implement and preserve)

The GUI must support the following keyboard shortcuts for tool / analysis selection:

**Tool selection shortcuts (single key, no modifier):**
- `W` → Activate **Wire** tool.
- `R` → Activate **Resistor placement** tool.
- `C` → Activate **Capacitor placement** tool.
- `L` → Activate **Inductor placement** tool.
- `D` → Activate **Diode placement** tool.
- `B` → Activate **BJT transistor placement** tool.
- `M` → Activate **MOSFET placement** tool.
- `O` → Activate **Op-amp placement** tool.
- `V` → Activate **Voltage source placement** tool.
- `G` → Activate **Ground symbol placement** tool.
- `N` → Activate **Net label** tool.

Pressing these keys should select the corresponding mode/tool in the schematic editor, exactly as if the user clicked the matching toolbar button. They MUST NOT create components instantly at the current mouse position; they only change the active placement mode.

**Analysis / simulation shortcuts (with modifiers):**
- `Ctrl + Alt + D` → Run **DC analysis** (or open DC analysis dialog; currently may be a stub).
- `Ctrl + Alt + A` → Run **AC analysis**.
- `Ctrl + Alt + T` → Run **Transient analysis**.
- `Ctrl + Alt + N` → Run **Noise analysis**.
- `Ctrl + Alt + F` → Run **FFT / THD analysis**.

These shortcuts must trigger the same slots as the corresponding toolbar/menu actions (Run DC, Run AC, etc.). If those actions are not implemented yet, the slots may show a “Not implemented yet” message, but the shortcut bindings must still exist and be kept consistent.

Cursor must:
- Use Qt’s `QAction.setShortcut(QKeySequence("..."))` (or equivalent) to register these shortcuts.
- Ensure shortcuts are attached to actions that live on `QMainWindow` (so they are active when the main window has focus).
- Avoid conflicting shortcuts and do not override these mappings in future edits.

---

### 2.4 Schematic Symbols (SVG, used in canvas)

The schematic editor must use vector symbols loaded from SVG files for drawing components on the canvas (not the toolbar icons).

- All schematic symbols must be loaded from `src/resources/symbols/`.
- One SVG per symbol, e.g.:
  - `resistor.svg`
  - `capacitor.svg`
  - `inductor.svg`
  - `diode.svg`
  - `bjt.svg`
  - `mosfet.svg`
  - `voltage_source.svg`
  - `current_source.svg`
  - `opamp.svg`
  - `ground.svg`
  - `net_label.svg`
  - `vccs.svg`
- These SVGs are used only for the **schematic canvas rendering** (the QGraphicsView), not for the toolbar buttons.
- Existing procedural / placeholder drawing code for R, C, V, I, etc. should be gradually replaced by SVG-based rendering that:
  - keeps pin locations and connectivity consistent,
  - supports rotation and mirroring,
  - and uses `SchematicComponent.ctype` to select the correct symbol.


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

### 3.1 Interaction Behavior (Move vs Select)

- When the user **clicks and drags starting on a component**:
  - The operation MUST be treated as a component move.
  - No drag-selection rectangle should appear.
  - If multiple components are selected, they move as a group.
- When the user **clicks and drags starting on empty space**:
  - The operation MUST be treated as drag-selection (rubber-band selection).
- Cursor MUST implement hit-testing on mouse press to decide:
  - “Press on item” → move mode (no rubber-band)
  - “Press on background” → selection mode (rubber-band)
- This rule must be preserved whenever changing `mousePressEvent` / drag behavior.

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


## 11. Python Indentation & Formatting Rules (Cursor MUST follow)

To ensure consistent and valid Python code across all modules, Cursor must enforce the following formatting rules at all times:

### 11.1 Indentation Requirements
- All Python code MUST use **4 spaces per indentation level**.
- **Tabs are strictly forbidden**.
- Mixing tabs and spaces is forbidden.
- Cursor MUST convert any tabs to spaces when encountered.

### 11.2 File Rewriting Policy
- If indentation becomes ambiguous or inconsistent, Cursor MUST:
  1. Stop generating partial diffs.
  2. Rewrite the entire affected file with correct indentation.
  3. Preserve behavior but normalize structure and formatting.

### 11.3 Patch-Level Behavior
When applying diffs:
- Cursor must align indentation with the surrounding context.
- New code blocks must follow correct Python block indentation.
- Nested blocks must maintain correct structure (class, def, if/else, loops).
- Cursor must not introduce misaligned blocks, accidental dedentation, or over-indentation.

### 11.4 Safety Rules
- When indentation errors appear in the output (visible or inferred), Cursor must automatically correct indentation before finalizing the patch.
- When unsure of the correct indentation level, Cursor must rewrite the entire function or file for consistency.
- All files must remain PEP8-compliant in indentation and whitespace.

### 11.5 Editor Configuration
Cursor must respect the following:
- Trailing whitespace removed automatically.
- Newline at end of file.
- Continuation lines aligned with parentheses or 4-space blocks.

### 11.6 Schematic Editor & GUI Specifics
Because GUI code (PySide6/QGraphicsView) often contains deep nesting:
- Cursor must prefer rewriting entire methods when indentation becomes unstable.
- Always validate indentation after modifying event handlers, draw routines, or nested layout structures.



End of rules.
