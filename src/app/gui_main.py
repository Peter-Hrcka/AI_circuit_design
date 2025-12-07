from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QInputDialog,
    QDialog,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtGui import QShortcut, QKeySequence

from app.schematic_view import SchematicView
from app.component_properties_dialog import ComponentPropertiesDialog
from core.netlist import (
    non_inverting_opamp_template,
    attach_vendor_opamp_model,
    build_non_inverting_ac_netlist,
    build_ac_sweep_netlist,
    build_noise_netlist,
    build_dc_netlist,
)
from core.model_analyzer import analyze_model
from core.model_conversion import maybe_convert_to_simple_opamp
from core.model_metadata import ModelMetadata
from core.simulator_manager import default_simulator_manager as sims
from core.optimization import (
    optimize_gain_for_non_inverting_stage,
    optimize_gain_spice_loop,
    measure_gain_spice,
)
from core.analysis import find_3db_bandwidth
from core.schematic_generate import non_inverting_circuit_to_schematic
from core.schematic_to_circuit import (
    circuit_from_non_inverting_schematic,  # For backward compatibility
    circuit_from_schematic,  # General converter
)
from core.schematic_validation import validate_schematic, ValidationError
from core.schematic_model import SchematicModel


def _build_node_to_net_mapping(model: SchematicModel, circuit) -> dict[str, list[str]]:
    """
    Build a mapping from circuit node names to schematic net names.
    
    Returns:
        Dictionary mapping circuit node name -> list of schematic net names
        (a circuit node might correspond to multiple schematic nets that were merged)
    """
    from core.schematic_to_circuit import _canon_net
    
    # Reverse mapping: circuit node -> schematic nets
    node_to_nets: dict[str, list[str]] = {}
    
    # Process all components to find which schematic nets map to which circuit nodes
    for comp in model.components:
        for pin in comp.pins:
            if pin.net:
                schematic_net = pin.net
                circuit_node = _canon_net(schematic_net)
                
                # Build reverse mapping
                if circuit_node not in node_to_nets:
                    node_to_nets[circuit_node] = []
                if schematic_net not in node_to_nets[circuit_node]:
                    node_to_nets[circuit_node].append(schematic_net)
    
    return node_to_nets


def format_schematic_netlist(model: SchematicModel) -> str:
    """
    Format the schematic model as a readable text representation showing
    component pins, nets, and connections.
    """
    lines = []
    lines.append("=" * 70)
    lines.append("SCHEMATIC NETLIST INFORMATION")
    lines.append("=" * 70)
    lines.append("")
    
    # Extract nets first
    from core.net_extraction import extract_nets_with_intersections
    extract_nets_with_intersections(model)
    
    lines.append("COMPONENTS:")
    lines.append("-" * 70)
    for comp in model.components:
        lines.append(f"  {comp.ref} ({comp.ctype}): value={comp.value}")
        for pin in comp.pins:
            net_str = pin.net if pin.net else "NO NET"
            lines.append(f"    Pin {pin.name}: net={net_str} at ({pin.x:.1f}, {pin.y:.1f})")
        lines.append("")
    
    lines.append("")
    lines.append("WIRES:")
    lines.append("-" * 70)
    for wire in model.wires:
        net_str = wire.net if wire.net else "NO NET"
        lines.append(f"  Wire: net={net_str} from ({wire.x1:.1f}, {wire.y1:.1f}) to ({wire.x2:.1f}, {wire.y2:.1f})")
    
    lines.append("")
    lines.append("JUNCTIONS:")
    lines.append("-" * 70)
    for junction in model.junctions:
        net_str = junction.net if junction.net else "NO NET"
        lines.append(f"  Junction: net={net_str} at ({junction.x:.1f}, {junction.y:.1f})")
    
    lines.append("")
    lines.append("NET SUMMARY:")
    lines.append("-" * 70)
    # Group components by net
    nets = {}
    for comp in model.components:
        for pin in comp.pins:
            if pin.net:
                if pin.net not in nets:
                    nets[pin.net] = []
                nets[pin.net].append(f"{comp.ref}.{pin.name}")
    
    for net_name in sorted(nets.keys()):
        pins = nets[net_name]
        lines.append(f"  Net {net_name}: {', '.join(pins)}")
    
    lines.append("")
    lines.append("=" * 70)
    lines.append("ATTEMPTED CIRCUIT CONVERSION:")
    lines.append("-" * 70)
    try:
        circuit = circuit_from_schematic(model)
        lines.append(f"Circuit name: {circuit.name}")
        lines.append("")
        lines.append("Circuit components (SPICE format):")
        for comp in circuit.components:
            if comp.ctype == "R":
                lines.append(f"  {comp.ref} {comp.node1} {comp.node2} {comp.value}")
            elif comp.ctype == "C":
                lines.append(f"  {comp.ref} {comp.node1} {comp.node2} {comp.value}")
            elif comp.ctype == "V":
                dc = comp.extra.get("dc_level", comp.value)
                ac = comp.extra.get("ac_amplitude", 0.0)
                if ac > 0:
                    lines.append(f"  {comp.ref} {comp.node1} {comp.node2} DC {dc} AC {ac}")
                else:
                    lines.append(f"  {comp.ref} {comp.node1} {comp.node2} DC {dc}")
            elif comp.ctype == "OPAMP":
                out_node = comp.extra.get("output_node", "OUT")
                lines.append(f"  * {comp.ref}: OPAMP +={comp.node1} -={comp.node2} OUT={out_node}")
                lines.append(f"  * (Op-amp is expanded to subcircuit, see netlist builder)")
            elif comp.ctype == "GND":
                lines.append(f"  * {comp.ref}: GND (ground reference - not a SPICE component)")
            else:
                lines.append(f"  {comp.ref} {comp.node1} {comp.node2} {comp.value}")
    except Exception as exc:
        lines.append(f"  ERROR converting to circuit: {exc}")
    
    lines.append("")
    lines.append("=" * 70)
    
    return "\n".join(lines)


class ValidationErrorDialog(QDialog):
    """Custom dialog showing validation errors and schematic details."""
    
    def __init__(self, parent, errors: list[ValidationError], model: SchematicModel):
        super().__init__(parent)
        self.setWindowTitle("Schematic Validation Error")
        self.setMinimumSize(700, 500)
        
        layout = QVBoxLayout(self)
        
        # Create tab widget
        tabs = QTabWidget()
        
        # Errors tab
        errors_tab = QWidget()
        errors_layout = QVBoxLayout(errors_tab)
        error_label = QLabel("Validation Errors:")
        error_label.setStyleSheet("font-weight: bold;")
        errors_layout.addWidget(error_label)
        
        error_text = QTextEdit()
        error_text.setReadOnly(True)
        error_messages = [err.message for err in errors]
        error_text.setPlainText("Schematic validation failed:\n\n" + "\n".join(f"• {msg}" for msg in error_messages))
        errors_layout.addWidget(error_text)
        
        tabs.addTab(errors_tab, "Errors")
        
        # Netlist/Details tab
        details_tab = QWidget()
        details_layout = QVBoxLayout(details_tab)
        details_label = QLabel("Schematic Netlist Details:")
        details_label.setStyleSheet("font-weight: bold;")
        details_layout.addWidget(details_label)
        
        details_text = QTextEdit()
        details_text.setReadOnly(True)
        details_text.setFont(QLabel().font())  # Use monospace if available
        try:
            netlist_str = format_schematic_netlist(model)
            details_text.setPlainText(netlist_str)
        except Exception as exc:
            details_text.setPlainText(f"Error generating netlist details:\n{exc}")
        details_layout.addWidget(details_text)
        
        tabs.addTab(details_tab, "Netlist Details")
        
        layout.addWidget(tabs)
        
        # OK button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.current_circuit = None  # will hold last simulated Circuit
        self.last_model_meta = None  # type: Optional[ModelMetadata]
        self.last_freq_hz: float | None = None
        self.last_target_gain_db: float | None = None
        self.selected_component_ref = None  # Currently selected component for rotation
        self.setWindowTitle("AI Circuit Designer")

        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)

        # --- Model file selector -------------------------------------------------
        file_row = QHBoxLayout()
        layout.addLayout(file_row)

        file_row.addWidget(QLabel("Op-amp model (.lib):"))
        self.model_path_edit = QLineEdit()
        file_row.addWidget(self.model_path_edit)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.on_browse_model)
        file_row.addWidget(browse_btn)

        # --- Target gain ---------------------------------------------------------
        gain_row = QHBoxLayout()
        layout.addLayout(gain_row)

        gain_row.addWidget(QLabel("Target gain (dB):"))
        self.target_gain_edit = QLineEdit("40.0")
        gain_row.addWidget(self.target_gain_edit)

        # --- Test frequency ------------------------------------------------------
        freq_row = QHBoxLayout()
        layout.addLayout(freq_row)

        freq_row.addWidget(QLabel("Test frequency (Hz):"))
        self.freq_edit = QLineEdit("1000000")  # 1 MHz by default
        freq_row.addWidget(self.freq_edit)

        # --- Run buttons ----------------------------------------------------------
        run_row = QHBoxLayout()
        layout.addLayout(run_row)

        run_btn = QPushButton("Run optimization")
        run_btn.clicked.connect(self.on_run)
        run_row.addWidget(run_btn)

        resim_btn = QPushButton("Re-simulate current schematic")
        resim_btn.clicked.connect(self.on_resimulate_current)
        run_row.addWidget(resim_btn)

        sim_schem_btn = QPushButton("Simulate FROM schematic (no optimization)")
        sim_schem_btn.clicked.connect(self.on_simulate_from_schematic)
        run_row.addWidget(sim_schem_btn)
        
        dc_btn = QPushButton("DC Analysis")
        dc_btn.clicked.connect(self.on_dc_analysis)
        run_row.addWidget(dc_btn)



        # --- Component Palette / Toolbox ----------------------------------------
        palette_row = QHBoxLayout()
        layout.addLayout(palette_row)
        
        palette_row.addWidget(QLabel("Components:"))
        
        # Component placement buttons - create mapping first
        self._placement_button_to_type = {}
        
        self.btn_place_resistor = QPushButton("Resistor")
        self.btn_place_resistor.setCheckable(True)
        self._placement_button_to_type[self.btn_place_resistor] = "R"
        self.btn_place_resistor.clicked.connect(self.on_place_component_clicked)
        palette_row.addWidget(self.btn_place_resistor)
        
        self.btn_place_capacitor = QPushButton("Capacitor")
        self.btn_place_capacitor.setCheckable(True)
        self._placement_button_to_type[self.btn_place_capacitor] = "C"
        self.btn_place_capacitor.clicked.connect(self.on_place_component_clicked)
        palette_row.addWidget(self.btn_place_capacitor)
        
        self.btn_place_opamp = QPushButton("Op-amp")
        self.btn_place_opamp.setCheckable(True)
        self._placement_button_to_type[self.btn_place_opamp] = "OPAMP"
        self.btn_place_opamp.clicked.connect(self.on_place_component_clicked)
        palette_row.addWidget(self.btn_place_opamp)
        
        self.btn_place_voltage = QPushButton("Voltage Source")
        self.btn_place_voltage.setCheckable(True)
        self._placement_button_to_type[self.btn_place_voltage] = "V"
        self.btn_place_voltage.clicked.connect(self.on_place_component_clicked)
        palette_row.addWidget(self.btn_place_voltage)
        
        self.btn_place_current = QPushButton("Current Source")
        self.btn_place_current.setCheckable(True)
        self._placement_button_to_type[self.btn_place_current] = "I"
        self.btn_place_current.clicked.connect(self.on_place_component_clicked)
        palette_row.addWidget(self.btn_place_current)
        
        self.btn_place_ground = QPushButton("Ground")
        self.btn_place_ground.setCheckable(True)
        self._placement_button_to_type[self.btn_place_ground] = "GND"
        self.btn_place_ground.clicked.connect(self.on_place_component_clicked)
        palette_row.addWidget(self.btn_place_ground)
        
        self.btn_place_vout = QPushButton("Vout")
        self.btn_place_vout.setCheckable(True)
        self._placement_button_to_type[self.btn_place_vout] = "VOUT"
        self.btn_place_vout.clicked.connect(self.on_place_component_clicked)
        palette_row.addWidget(self.btn_place_vout)
        
        palette_row.addWidget(QLabel("|"))
        
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setCheckable(True)
        self.btn_delete.clicked.connect(self.on_delete_mode)
        palette_row.addWidget(self.btn_delete)
        
        palette_row.addStretch()

        # --- Toolbox: interaction mode -----------------------------------------
        tool_row = QHBoxLayout()
        layout.addLayout(tool_row)

        tool_row.addWidget(QLabel("Mode:"))

        self.btn_mode_select = QPushButton("Select / Edit")
        self.btn_mode_select.setCheckable(True)
        self.btn_mode_select.setChecked(True)
        self.btn_mode_select.clicked.connect(self.on_mode_select)
        tool_row.addWidget(self.btn_mode_select)

        self.btn_mode_wire = QPushButton("Wire")
        self.btn_mode_wire.setCheckable(True)
        self.btn_mode_wire.clicked.connect(self.on_mode_wire)
        tool_row.addWidget(self.btn_mode_wire)

        tool_row.addWidget(QLabel("|"))
        
        self.btn_rotate = QPushButton("Rotate 90°")
        self.btn_rotate.clicked.connect(self.on_rotate_component)
        tool_row.addWidget(self.btn_rotate)

        tool_row.addWidget(QLabel("|"))
        
        self.btn_rotate = QPushButton("Rotate 90°")
        self.btn_rotate.clicked.connect(self.on_rotate_component)
        tool_row.addWidget(self.btn_rotate)

        tool_row.addStretch()
        
        # Store component placement buttons for mode management
        self._placement_buttons = [
            self.btn_place_resistor,
            self.btn_place_capacitor,
            self.btn_place_opamp,
            self.btn_place_voltage,
            self.btn_place_ground,
            self.btn_place_vout,
        ]


        # --- Tabs: Log + Schematic ----------------------------------------------
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Log tab
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.addWidget(QLabel("Log:"))
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        log_layout.addWidget(self.output)
        self.tabs.addTab(log_widget, "Log")

        # Schematic tab
        self.schematic_view = SchematicView()
        self.tabs.addTab(self.schematic_view, "Schematic")
        
        # Set Schematic tab as default (index 1, since Log is index 0)
        self.tabs.setCurrentIndex(1)

        # React to clicks on components in the schematic
        self.schematic_view.componentClicked.connect(self.on_component_clicked)
        
        # Setup keyboard shortcuts
        self._setup_keyboard_shortcuts()
    


    # --------------------------------------------------------------------- #
    # Helpers
    # --------------------------------------------------------------------- #


    def log(self, text: str) -> None:
        """Append a line to the output box."""
        self.output.append(text)

    def on_browse_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select op-amp model (.lib)",
            "",
            "SPICE Models (*.lib *.cir *.sub *.sp *.spi);;All Files (*.*)",
        )
        if path:
            self.model_path_edit.setText(path)

    def _load_model_with_conversion(self, path: str) -> Optional[ModelMetadata]:
        try:
            meta_orig = analyze_model(path)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to read model:\n{exc}")
            return None

        self.log("Model analysis:")
        self.log(f"  Summary: {meta_orig.short_summary()}")
        self.log(f"  Recommended simulator: {meta_orig.recommended_simulator}")
        self.log(f"  Vendor: {meta_orig.vendor}")
        self.log(f"  Models: {meta_orig.model_names}")
        self.log("")

        meta_conv = maybe_convert_to_simple_opamp(meta_orig, auto_for_nonstandard=True)

        if meta_conv.path != meta_orig.path:
            self.log("Model conversion:")
            self.log(f"  Original file:   {meta_orig.path}")
            self.log(f"  Simplified file: {meta_conv.path}")
            for w in meta_conv.conversion_warnings:
                self.log(f"  Warning: {w}")
            self.log("")
        else:
            self.log("No conversion applied (model is standard SPICE or auto-conversion disabled).")
            self.log("")

        return meta_conv

    # --------------------------------------------------------------------- #
    # Main action
    # --------------------------------------------------------------------- #

    def on_run(self) -> None:
        self.output.clear()

        model_path = self.model_path_edit.text().strip()
        if not model_path:
            QMessageBox.warning(self, "Missing model", "Please select a vendor model (.lib) first.")
            return

        try:
            target_gain_db = float(self.target_gain_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid gain", "Please enter a numeric target gain (dB).")
            return

        try:
            freq_hz = float(self.freq_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid frequency", "Please enter a numeric frequency (Hz).")
            return

        model_path = str(Path(model_path))  # normalize

        # 1) Analyze & maybe convert model
        meta_model = self._load_model_with_conversion(model_path)
        if meta_model is None:
            return
        self.last_model_meta = meta_model
        self.last_freq_hz = freq_hz
        self.last_target_gain_db = target_gain_db

        # 2) Build initial circuit
        circuit = non_inverting_opamp_template()

        self.log("Initial circuit:")
        for comp in circuit.components:
            self.log(
                f"  {comp.ref}: {comp.ctype} {comp.value} {comp.unit} "
                f"{comp.node1} {comp.node2}"
            )
        self.log("")

        # Choose subckt name to use when instantiating:
        if meta_model.model_names:
            subckt_name = meta_model.model_names[0]
        else:
            subckt_name = Path(meta_model.path).stem

        # Attach the model to the circuit
        attach_vendor_opamp_model(
            circuit,
            model_file=meta_model.path,
            subckt_name=subckt_name,
            meta=meta_model,
        )

        # 3) Ideal optimization
        ideal_circuit, ideal_gain_db = optimize_gain_for_non_inverting_stage(
            circuit,
            target_gain_db=target_gain_db,
        )

        self.log("After ideal (symbolic) optimization:")
        for comp in ideal_circuit.components:
            self.log(
                f"  {comp.ref}: {comp.ctype} {comp.value} {comp.unit} "
                f"{comp.node1} {comp.node2}"
            )
        self.log(f"Target gain (ideal):   {target_gain_db:.2f} dB")
        self.log(f"Achieved (ideal):      {ideal_gain_db:.2f} dB")
        self.log("")

        # 4) SPICE-in-the-loop optimization
        self.log("Running SPICE-in-the-loop optimization...")
        try:
            final_circuit, measured_gain_db, iters = optimize_gain_spice_loop(
                ideal_circuit,
                target_gain_db=target_gain_db,
                freq_hz=freq_hz,
                max_iterations=5,
                tolerance_db=0.1,
                model_meta=meta_model,
            )
            self._update_schematic_from_circuit(final_circuit)
            self.current_circuit = final_circuit

            # Note: We don't auto-populate the schematic - it should remain independent
            # User can build their own schematic and simulate it separately


        except Exception as exc:
            QMessageBox.critical(self, "SPICE error", f"Error during SPICE optimization:\n{exc}")
            return

        self.log("")
        self.log("Final circuit after SPICE-in-the-loop:")
        for comp in final_circuit.components:
            self.log(
                f"  {comp.ref}: {comp.ctype} {comp.value} {comp.unit} "
                f"{comp.node1} {comp.node2}"
            )
        self.log("")
        self.log(f"SPICE frequency:       {freq_hz:.1f} Hz")
        self.log(f"Target gain (SPICE):   {target_gain_db:.2f} dB")
        self.log(f"Achieved (SPICE):      {measured_gain_db:.2f} dB")
        self.log(f"Iterations used:       {iters}")
        self.log("")

        # 5) Bandwidth via AC sweep
        self.log("Running AC sweep for bandwidth...")
        ac_net = build_ac_sweep_netlist(final_circuit)
        ac_res = sims.run_ac_sweep(ac_net, meta_model)
        bw = find_3db_bandwidth(ac_res["freq_hz"], ac_res["gain_db"])
        if bw is None:
            self.log("Bandwidth (-3 dB): > sweep range (no rolloff found)")
        else:
            self.log(f"Bandwidth (-3 dB): {bw/1000:.2f} kHz")
        self.log("")

        # 6) Noise analysis
        self.log("Running noise analysis (10 Hz – 20 kHz).")
        noise_net = build_noise_netlist(final_circuit)
        noise_res = sims.run_noise_sweep(noise_net, meta_model)

        onoise = noise_res["total_onoise_rms"]
        inoise = noise_res["total_inoise_rms"]
        self.log(f"Total output noise 10 Hz–20 kHz: {onoise*1e6:.2f} µV_rms")
        self.log(f"Equivalent input noise 10 Hz–20 kHz: {inoise*1e9:.2f} nV_rms")
        self.log("Done.")

    def _update_schematic_from_circuit(self, circuit):
        """Update schematic component values from circuit. Safe to call with None."""
        if circuit is None:
            return
        
        rin = r1 = r2 = None

        for comp in circuit.components:
            ref = getattr(comp, "ref", "")
            value = getattr(comp, "value", None)
            if ref == "Rin":
                rin = value
            elif ref == "R1":
                r1 = value
            elif ref == "R2":
                r2 = value

        if hasattr(self, "schematic_view"):
            self.schematic_view.set_component_values(rin=rin, r1=r1, r2=r2)

            # NEW: sync underlying model values as well
            self.schematic_view.sync_values_from_circuit(circuit)

    def on_component_clicked(self, ref: str) -> None:
        """
        Called when user clicks a component in the schematic, e.g. "R1".
        Opens a comprehensive properties dialog to edit component properties.
        """
        # Store selected component ref for rotation
        self.selected_component_ref = ref
        
        # Find component in schematic model
        schematic_comp = None
        if hasattr(self, "schematic_view") and self.schematic_view.model:
            for comp in self.schematic_view.model.components:
                if comp.ref == ref:
                    schematic_comp = comp
                    break

        if schematic_comp is None:
            QMessageBox.warning(self, "Not found", f"Component {ref} not found in schematic.")
            return

        # Show comprehensive properties dialog
        dialog = ComponentPropertiesDialog(schematic_comp, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return  # User cancelled
        
        # Update component properties from dialog
        properties = dialog.result_properties
        
        # Update value
        if "value" in properties:
            schematic_comp.value = properties["value"]
        
        # Update extra properties
        if schematic_comp.ctype == "C":
            # Capacitor: tolerance, ESR
            schematic_comp.extra["tolerance"] = properties.get("tolerance", 0.0)
            schematic_comp.extra["esr"] = properties.get("esr", 0.0)
        
        elif schematic_comp.ctype == "OPAMP":
            # Op-amp: model file, supply rails
            # Handle model_file: if None, remove it; otherwise set it
            if "model_file" in properties:
                model_file = properties["model_file"]
                if model_file:
                    schematic_comp.extra["model_file"] = model_file
                else:
                    # Explicitly cleared - remove it
                    schematic_comp.extra.pop("model_file", None)
            # Supply rails are always set
            schematic_comp.extra["vcc"] = properties.get("vcc", 15.0)
            schematic_comp.extra["vee"] = properties.get("vee", -15.0)
        
        elif schematic_comp.ctype == "V":
            # Voltage source: DC level, AC amplitude
            if "dc_level" in properties:
                schematic_comp.value = properties["dc_level"]
            schematic_comp.extra["ac_amplitude"] = properties.get("ac_amplitude", 0.0)
        
        elif schematic_comp.ctype == "I":
            # Current source: DC level (current value)
            if "dc_level" in properties:
                schematic_comp.value = properties["dc_level"]
            elif "value" in properties:
                schematic_comp.value = properties["value"]
        
        # Update circuit if it exists (sync values)
        if self.current_circuit:
            circuit_comp = None
            for comp in self.current_circuit.components:
                if getattr(comp, "ref", "") == ref:
                    circuit_comp = comp
                    break
            
            if circuit_comp and "value" in properties:
                circuit_comp.value = properties["value"]
                # Update schematic labels from circuit
                self._update_schematic_from_circuit(self.current_circuit)

        # Redraw schematic to show updated values
        if hasattr(self, "schematic_view"):
            self.schematic_view._redraw_from_model()
        
        # Log the change
        prop_str = ", ".join(f"{k}={v}" for k, v in properties.items())
        self.log(f"{ref} properties updated: {prop_str}. "
                 "Re-run analysis if you want updated SPICE results.")

    def on_rotate_component(self):
        """Rotate the selected component by 90 degrees."""
        if not self.selected_component_ref:
            QMessageBox.information(self, "No selection", "Please select a component first by clicking on it.")
            return
        
        # Rotate component in schematic view
        if hasattr(self, "schematic_view"):
            success = self.schematic_view.rotate_component(self.selected_component_ref)
            if success:
                self.log(f"Rotated {self.selected_component_ref} by 90°")
            else:
                QMessageBox.warning(self, "Rotation failed", f"Could not rotate component {self.selected_component_ref}.")

    def on_resimulate_current(self) -> None:
        """
        Re-run SPICE analysis on the current schematic,
        using the last model + frequency (or defaults), without optimization.
        """
        if self.schematic_view.model is None:
            QMessageBox.information(self, "No schematic",
                                    "No schematic model available. Please build a circuit first.")
            return

        # Get frequency - use stored or prompt user
        freq_hz = self.last_freq_hz
        if freq_hz is None:
            # Use default or get from user
            freq_hz, ok = QInputDialog.getDouble(
                self,
                "Simulation Frequency",
                "Enter test frequency (Hz):",
                1000000.0,  # Default 1 MHz
                1.0,
                1e12,
                0,
            )
            if not ok:
                return
            self.last_freq_hz = freq_hz

        # Get model metadata - use stored or use built-in
        meta_model = self.last_model_meta
        if meta_model is None:
            # Use built-in model (no model file needed)
            meta_model = None  # Will use built-in op-amp model
            self.log("Using built-in op-amp model (no external model file loaded).")

        # Validate schematic before simulation
        from core.schematic_validation import validate_schematic
        is_valid, validation_errors = validate_schematic(self.schematic_view.model)
        
        if not is_valid:
            dialog = ValidationErrorDialog(self, validation_errors, self.schematic_view.model)
            dialog.exec()
            return

        # Build circuit from schematic model
        try:
            circuit = circuit_from_schematic(self.schematic_view.model)
            self.current_circuit = circuit  # Store for future use
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Schematic error",
                f"Could not build circuit from schematic:\n{exc}",
            )
            return

        self.log("")
        self.log("Re-simulating current schematic...")

        try:
            measured_gain_db = measure_gain_spice(
                circuit,
                freq_hz=freq_hz,
                model_meta=meta_model,
            )
        except Exception as exc:
            QMessageBox.critical(self, "SPICE error", f"Error during SPICE re-simulation:\n{exc}")
            return

        # Log current circuit values
        self.log("Current circuit for re-simulation:")
        for comp in circuit.components:
            self.log(
                f"  {comp.ref}: {comp.ctype} {comp.value} {comp.unit} "
                f"{comp.node1} {comp.node2}"
            )

        self.log(f"SPICE frequency:       {freq_hz:.1f} Hz")
        self.log(f"Achieved (SPICE):      {measured_gain_db:.2f} dB")

        # AC sweep with current values
        if meta_model is not None:  # Only if we have a model
            self.log("")
            self.log("Running AC sweep for bandwidth (current schematic)...")
            ac_net = build_ac_sweep_netlist(circuit)
            ac_res = sims.run_ac_sweep(ac_net, meta_model)
            bw = find_3db_bandwidth(ac_res["freq_hz"], ac_res["gain_db"])
            if bw is None:
                self.log("Bandwidth (-3 dB): > sweep range (no rolloff found)")
            else:
                self.log(f"Bandwidth (-3 dB): {bw/1000:.2f} kHz")

        # Noise analysis with current values
        self.log("")
        self.log("Running noise analysis (10 Hz – 20 kHz, current schematic).")
        noise_net = build_noise_netlist(self.current_circuit)
        noise_res = sims.run_noise_sweep(noise_net, meta_model)

        onoise = noise_res["total_onoise_rms"]
        inoise = noise_res["total_inoise_rms"]
        self.log(f"Total output noise 10 Hz–20 kHz: {onoise*1e6:.2f} µV_rms")
        self.log(f"Equivalent input noise 10 Hz–20 kHz: {inoise*1e9:.2f} nV_rms")
        self.log("Re-simulation done.")

    def _update_mode_buttons(self, select_active: bool):
        self.btn_mode_select.setChecked(select_active)
        self.btn_mode_wire.setChecked(not select_active)

    def on_mode_select(self):
        self._update_mode_buttons(True)
        self.schematic_view.set_mode("select")

    def on_mode_wire(self):
        self._update_mode_buttons(False)
        self._clear_placement_buttons()
        self.schematic_view.set_mode("wire")

    def on_place_component_clicked(self):
        """Handle component placement button click."""
        sender_btn = self.sender()
        if sender_btn is None:
            return
        
        component_type = self._placement_button_to_type.get(sender_btn)
        if component_type is None:
            return
        
        # Uncheck all other placement buttons
        for btn in self._placement_buttons:
            if btn != sender_btn:
                btn.setChecked(False)
        
        # Uncheck mode buttons
        self.btn_mode_select.setChecked(False)
        self.btn_mode_wire.setChecked(False)
        
        # Set placement mode in schematic view
        if sender_btn.isChecked():
            self.schematic_view.set_placement_mode(component_type)
        else:
            # If unchecking, go back to select mode
            self.btn_mode_select.setChecked(True)
            self.schematic_view.set_mode("select")

    def _setup_keyboard_shortcuts(self):
        """Setup keyboard shortcuts for component placement and modes."""
        # Component placement shortcuts
        QShortcut(QKeySequence("R"), self, activated=lambda: self._place_component_by_key("R"))
        QShortcut(QKeySequence("C"), self, activated=lambda: self._place_component_by_key("C"))
        QShortcut(QKeySequence("O"), self, activated=lambda: self._place_component_by_key("OPAMP"))
        QShortcut(QKeySequence("V"), self, activated=lambda: self._place_component_by_key("V"))
        QShortcut(QKeySequence("I"), self, activated=lambda: self._place_component_by_key("I"))
        QShortcut(QKeySequence("G"), self, activated=lambda: self._place_component_by_key("GND"))
        
        # Wire mode shortcut
        QShortcut(QKeySequence("W"), self, activated=self._activate_wire_mode)
        
        # Delete/Escape to reset mode
        QShortcut(QKeySequence("Delete"), self, activated=self._reset_mode)
        QShortcut(QKeySequence("Escape"), self, activated=self._reset_mode)
    
    def _place_component_by_key(self, component_type: str):
        """Place a component using keyboard shortcut."""
        # Find the corresponding button
        button = None
        for btn, ctype in self._placement_button_to_type.items():
            if ctype == component_type:
                button = btn
                break
        
        if button:
            # Uncheck all other buttons first
            for btn in self._placement_buttons:
                if btn != button:
                    btn.setChecked(False)
            self.btn_mode_select.setChecked(False)
            self.btn_mode_wire.setChecked(False)
            
            # Toggle the button (this will trigger on_place_component_clicked)
            button.setChecked(True)
            button.clicked.emit()
            
            # Focus the schematic view so clicks work
            self.schematic_view.setFocus()
    
    def _activate_wire_mode(self):
        """Activate wire mode using keyboard shortcut."""
        # Uncheck all placement buttons
        for btn in self._placement_buttons:
            btn.setChecked(False)
        self.btn_mode_select.setChecked(False)
        
        # Activate wire mode
        self.btn_mode_wire.setChecked(True)
        self.btn_mode_wire.clicked.emit()
        
        # Focus the schematic view
        self.schematic_view.setFocus()
    
    def _reset_mode(self):
        """Reset to select mode (unselect current mode)."""
        # Uncheck all placement buttons
        for btn in self._placement_buttons:
            btn.setChecked(False)
        self.btn_delete.setChecked(False)
        
        # Uncheck mode buttons
        self.btn_mode_wire.setChecked(False)
        
        # Activate select mode
        self.btn_mode_select.setChecked(True)
        if hasattr(self, 'schematic_view'):
            self.schematic_view.set_mode("select")
        
        # Focus the schematic view
        if hasattr(self, 'schematic_view'):
            self.schematic_view.setFocus()

    def on_delete_mode(self):
        """Handle delete tool button click."""
        if self.sender().isChecked():
            # Uncheck all other buttons
            for btn in self._placement_buttons:
                btn.setChecked(False)
            self.btn_mode_select.setChecked(False)
            self.btn_mode_wire.setChecked(False)
            self.schematic_view.set_mode("delete")
        else:
            # If unchecking, go back to select mode
            self.btn_mode_select.setChecked(True)
            self.schematic_view.set_mode("select")

    def _clear_placement_buttons(self):
        """Uncheck all placement buttons."""
        for btn in self._placement_buttons:
            btn.setChecked(False)
        self.btn_delete.setChecked(False)

    def on_simulate_from_schematic(self) -> None:
        """
        Build a fresh Circuit from the current schematic model and run SPICE
        gain / bandwidth. No optimization, no value changes.
        Works independently - prompts for frequency/model if needed.
        """
        if self.schematic_view.model is None:
            QMessageBox.information(self, "No schematic",
                                    "No schematic model available.")
            return

        # Get frequency - use stored or prompt user
        freq_hz = self.last_freq_hz
        if freq_hz is None:
            freq_hz, ok = QInputDialog.getDouble(
                self,
                "Simulation Frequency",
                "Enter test frequency (Hz):",
                1000000.0,  # Default 1 MHz
                1.0,
                1e12,
                0,
            )
            if not ok:
                return
            self.last_freq_hz = freq_hz

        # Get model metadata - use stored or use built-in
        meta_model = self.last_model_meta
        if meta_model is None:
            # Use built-in model (no model file needed)
            meta_model = None  # Will use built-in op-amp model
            self.log("Using built-in op-amp model (no external model file loaded).")

        # Validate schematic before simulation
        is_valid, validation_errors = validate_schematic(self.schematic_view.model)
        
        if not is_valid:
            dialog = ValidationErrorDialog(self, validation_errors, self.schematic_view.model)
            dialog.exec()
            return

        # Build circuit from schematic model (general converter)
        try:
            circuit = circuit_from_schematic(self.schematic_view.model)
            self.current_circuit = circuit  # Store for future use
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Schematic error",
                f"Could not build circuit from schematic:\n{exc}",
            )
            return

        self.log("")
        self.log("Simulating circuit built FROM schematic (no optimization).")

        # Measure gain at freq_hz
        try:
            measured_gain_db = measure_gain_spice(
                circuit,
                freq_hz=freq_hz,
                model_meta=meta_model,
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "SPICE error",
                f"Error during SPICE simulation:\n{exc}",
            )
            return

        # Log circuit
        self.log("Circuit from schematic:")
        for comp in circuit.components:
            self.log(
                f"  {comp.ref}: {comp.ctype} {comp.value} {comp.unit} "
                f"{comp.node1} {comp.node2}"
            )

        self.log(f"SPICE frequency:       {freq_hz:.1f} Hz")
        self.log(f"Achieved gain (SPICE): {measured_gain_db:.2f} dB")

        # Optional: AC sweep with these values
        self.log("")
        self.log("Running AC sweep for bandwidth (schematic circuit).")
        ac_net = build_ac_sweep_netlist(circuit)
        ac_res = sims.run_ac_sweep(ac_net, meta_model)
        bw = find_3db_bandwidth(ac_res["freq_hz"], ac_res["gain_db"])
        if bw is None:
            self.log("Bandwidth (-3 dB): > sweep range (no rolloff found)")
        else:
            self.log(f"Bandwidth (-3 dB): {bw/1000:.2f} kHz")

    def on_dc_analysis(self) -> None:
        """
        Perform DC operating point analysis on the current schematic
        and display nodal voltages.
        """
        if self.schematic_view.model is None:
            QMessageBox.information(self, "No schematic",
                                    "No schematic model available. Please build a circuit first.")
            return
        
        # Validate schematic before simulation
        is_valid, validation_errors = validate_schematic(self.schematic_view.model)
        
        if not is_valid:
            dialog = ValidationErrorDialog(self, validation_errors, self.schematic_view.model)
            dialog.exec()
            return
        
        # Build circuit from schematic model
        try:
            circuit = circuit_from_schematic(self.schematic_view.model)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Schematic error",
                f"Could not build circuit from schematic:\n{exc}",
            )
            return
        
        self.log("")
        self.log("Running DC operating point analysis...")
        
        # Build DC netlist
        try:
            dc_netlist = build_dc_netlist(circuit)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Netlist error",
                f"Could not build DC netlist:\n{exc}",
            )
            return
        
        # Run DC analysis
        try:
            # Get model metadata (use stored or None for built-in)
            meta_model = self.last_model_meta
            nodal_voltages = sims.run_dc_analysis(dc_netlist, meta_model)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "DC Analysis Error",
                f"Error during DC analysis:\n{exc}",
            )
            return
        
        # Display results
        self.log("")
        self.log("DC Operating Point Analysis Results:")
        self.log("=" * 50)
        self.log(f"{'Node':<20} {'Voltage (V)':>15}")
        self.log("-" * 50)
        
        # Sort nodes for consistent display (ground first, then alphabetically)
        sorted_nodes = sorted(nodal_voltages.keys(), key=lambda n: (n != "0", n))
        for node in sorted_nodes:
            voltage = nodal_voltages[node]
            self.log(f"{node:<20} {voltage:>15.6f}")
        
        self.log("=" * 50)
        self.log("")
        
        # Update schematic view to display voltages
        # Map circuit node names back to schematic net names
        if hasattr(self, "schematic_view") and self.schematic_view.model:
            # Extract nets first to ensure all pins have net assignments
            from core.net_extraction import extract_nets_with_intersections
            extract_nets_with_intersections(self.schematic_view.model)
            
            # Build a mapping from circuit node names to schematic net names
            node_to_net_map = _build_node_to_net_mapping(self.schematic_view.model, circuit)
            
            # Debug: log the mapping and voltages
            self.log(f"DEBUG: DC analysis returned {len(nodal_voltages)} node voltages")
            self.log(f"DEBUG: Node to net map has {len(node_to_net_map)} entries")
            
            # Convert DC analysis results to use schematic net names
            # Also normalize case for matching
            schematic_voltages = {}
            
            # Create a case-insensitive lookup map for circuit nodes
            circuit_node_lower_map = {node.lower(): node for node in nodal_voltages.keys()}
            
            for circuit_node, voltage in nodal_voltages.items():
                # Find corresponding schematic net(s) for this circuit node
                # A circuit node might map to multiple schematic nets if they were merged
                schematic_nets = node_to_net_map.get(circuit_node, [])
                
                # Also try case-insensitive lookup in the mapping
                if not schematic_nets:
                    circuit_node_lower = circuit_node.lower()
                    for mapped_node, nets in node_to_net_map.items():
                        if mapped_node.lower() == circuit_node_lower:
                            schematic_nets = nets
                            break
                
                # Add voltage to all mapped schematic nets
                if schematic_nets:
                    for net in schematic_nets:
                        schematic_voltages[net] = voltage
                else:
                    # If no mapping found, use the circuit node name directly
                    # (in case it matches a schematic net name)
                    schematic_voltages[circuit_node] = voltage
            
            # Also create case-insensitive mappings for common nets
            # Map both uppercase and lowercase versions
            for net_name, voltage in list(schematic_voltages.items()):
                net_lower = net_name.lower()
                net_upper = net_name.upper()
                # Add lowercase version if original was uppercase
                if net_name == net_upper and net_lower not in schematic_voltages:
                    schematic_voltages[net_lower] = voltage
                # Add uppercase version if original was lowercase
                if net_name == net_lower and net_upper not in schematic_voltages:
                    schematic_voltages[net_upper] = voltage
            
            self.log(f"DEBUG: Mapped to {len(schematic_voltages)} schematic net voltages")
            for net, volt in sorted(schematic_voltages.items()):
                if net.lower() in ['n001', 'n002', 'n003', 'n004', '0'] or net == '0':
                    self.log(f"DEBUG: Net {net}: {volt:.3f}V")
            
            self.schematic_view.set_dc_voltages(schematic_voltages)


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(900, 600)
    win.show()
    sys.exit(app.exec())
    

if __name__ == "__main__":
    main()
