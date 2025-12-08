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
    QMenuBar,
    QMenu,
    QToolBar,
    QDockWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QListWidget,
    QListWidgetItem,
    QGroupBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QShortcut, QKeySequence, QAction, QIcon

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


def icon(name: str) -> QIcon:
    """
    Load an icon from the resources/icons directory.
    
    Args:
        name: Icon filename (e.g., "tool_pointer.svg", "comp_resistor.svg")
    
    Returns:
        QIcon object for the specified icon file
    """
    # Get the base directory (src/)
    base_dir = Path(__file__).parent.parent
    icon_path = base_dir / "resources" / "icons" / name
    return QIcon(str(icon_path))


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
        self.setWindowTitle("AI-Assisted Circuit Designer")

        # Initialize UI components
        self._setup_menu_bar()
        self._setup_toolbars()
        self._setup_central_widget()
        self._setup_left_dock()  # Component Library
        self._setup_right_dock()  # Properties / Analysis Setup
        self._setup_bottom_dock()  # Log / Results
        
        # Setup keyboard shortcuts
        self._setup_keyboard_shortcuts()
        
        # Note: model_path_edit, target_gain_edit, and freq_edit are created in _setup_right_dock()

    def _setup_menu_bar(self):
        """Create menu bar with File, Edit, View, Simulation, AI, Tools, Help menus."""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("File")
        file_menu.addAction("New", self._stub_action("New schematic"))
        file_menu.addAction("Open", self._stub_action("Open schematic"))
        file_menu.addAction("Save", self._stub_action("Save schematic"))
        file_menu.addAction("Save As...", self._stub_action("Save schematic as"))
        file_menu.addSeparator()
        file_menu.addAction("Import Model...", self.on_browse_model)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)
        
        # Edit menu
        edit_menu = menubar.addMenu("Edit")
        edit_menu.addAction("Undo", self._stub_action("Undo"))
        edit_menu.addAction("Redo", self._stub_action("Redo"))
        edit_menu.addSeparator()
        edit_menu.addAction("Cut", self._stub_action("Cut"))
        edit_menu.addAction("Copy", self._stub_action("Copy"))
        edit_menu.addAction("Paste", self._stub_action("Paste"))
        edit_menu.addSeparator()
        edit_menu.addAction("Select All", self._stub_action("Select all"))
        edit_menu.addAction("Delete", lambda: self.on_delete_mode() if hasattr(self, 'btn_delete') else self._stub_action("Delete"))
        
        # View menu
        view_menu = menubar.addMenu("View")
        view_menu.addAction("Zoom In", self._stub_action("Zoom in"))
        view_menu.addAction("Zoom Out", self._stub_action("Zoom out"))
        view_menu.addAction("Zoom Fit", self._stub_action("Zoom to fit"))
        view_menu.addSeparator()
        view_menu.addAction("Show Grid", self._stub_action("Toggle grid"))
        
        # Simulation menu
        self.sim_menu = menubar.addMenu("Simulation")
        # Note: Analysis actions are created in _setup_toolbars() and added here to share shortcuts
        # These will be added after toolbar setup is complete
        
        # AI menu
        ai_menu = menubar.addMenu("AI")
        ai_menu.addAction("AI Optimize", self._stub_action("AI Optimize"))
        ai_menu.addAction("AI Explain Circuit", self._stub_action("AI Explain Circuit"))
        
        # Tools menu
        tools_menu = menubar.addMenu("Tools")
        tools_menu.addAction("Component Properties", self._stub_action("Open component properties"))
        tools_menu.addAction("Netlist Preview", self._stub_action("Show netlist"))
        
        # Help menu
        help_menu = menubar.addMenu("Help")
        help_menu.addAction("About", self._stub_action("About"))
        help_menu.addAction("Documentation", self._stub_action("Documentation"))

    def _setup_toolbars(self):
        """Create main toolbar with editing tools (left) and simulation controls (right)."""
        # Main toolbar - Toolbar actions with SVG icons
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))  # Set consistent icon size
        self.addToolBar(toolbar)
        
        # Component placement actions - create mapping first
        self._placement_action_to_type = {}
        
        # Editing tools (left side)
        self.action_pointer = QAction(icon("tool_pointer.svg"), "Select", self)
        self.action_pointer.setCheckable(True)
        self.action_pointer.setChecked(True)
        self.action_pointer.setToolTip("Select/Move")
        self.action_pointer.triggered.connect(self.on_mode_select)
        toolbar.addAction(self.action_pointer)
        self.btn_mode_select = toolbar.widgetForAction(self.action_pointer)  # Keep reference for compatibility
        
        self.action_wire = QAction(icon("tool_wire.svg"), "Wire", self)
        self.action_wire.setCheckable(True)
        self.action_wire.setToolTip("Wire")
        self.action_wire.setShortcut(QKeySequence("W"))  # Keyboard shortcut for tool selection
        self.action_wire.triggered.connect(self.on_mode_wire)
        toolbar.addAction(self.action_wire)
        self.btn_mode_wire = toolbar.widgetForAction(self.action_wire)  # Keep reference for compatibility
        
        toolbar.addSeparator()
        
        # Component placement actions
        # Keyboard shortcuts for tool selection
        self.action_place_resistor = QAction(icon("comp_resistor.svg"), "Resistor", self)
        self.action_place_resistor.setCheckable(True)
        self.action_place_resistor.setToolTip("Resistor")
        self.action_place_resistor.setShortcut(QKeySequence("R"))  # Keyboard shortcut for tool selection
        self._placement_action_to_type[self.action_place_resistor] = "R"
        self.action_place_resistor.triggered.connect(self.on_place_component_clicked)
        toolbar.addAction(self.action_place_resistor)
        self.btn_place_resistor = toolbar.widgetForAction(self.action_place_resistor)
        
        self.action_place_capacitor = QAction(icon("comp_capacitor.svg"), "Capacitor", self)
        self.action_place_capacitor.setCheckable(True)
        self.action_place_capacitor.setToolTip("Capacitor")
        self.action_place_capacitor.setShortcut(QKeySequence("C"))  # Keyboard shortcut for tool selection
        self._placement_action_to_type[self.action_place_capacitor] = "C"
        self.action_place_capacitor.triggered.connect(self.on_place_component_clicked)
        toolbar.addAction(self.action_place_capacitor)
        self.btn_place_capacitor = toolbar.widgetForAction(self.action_place_capacitor)
        
        self.action_place_inductor = QAction(icon("comp_inductor.svg"), "Inductor", self)
        self.action_place_inductor.setCheckable(True)
        self.action_place_inductor.setToolTip("Inductor")
        self.action_place_inductor.setShortcut(QKeySequence("L"))  # Keyboard shortcut for tool selection
        self._placement_action_to_type[self.action_place_inductor] = "L"
        self.action_place_inductor.triggered.connect(self.on_place_component_clicked)
        toolbar.addAction(self.action_place_inductor)
        self.btn_place_inductor = toolbar.widgetForAction(self.action_place_inductor)
        
        self.action_place_diode = QAction(icon("comp_diode.svg"), "Diode", self)
        self.action_place_diode.setCheckable(True)
        self.action_place_diode.setToolTip("Diode")
        self.action_place_diode.setShortcut(QKeySequence("D"))  # Keyboard shortcut for tool selection
        self._placement_action_to_type[self.action_place_diode] = "D"
        self.action_place_diode.triggered.connect(self.on_place_component_clicked)
        toolbar.addAction(self.action_place_diode)
        self.btn_place_diode = toolbar.widgetForAction(self.action_place_diode)
        
        self.action_place_bjt = QAction(icon("comp_bjt.svg"), "BJT", self)
        self.action_place_bjt.setCheckable(True)
        self.action_place_bjt.setToolTip("BJT Transistor")
        self.action_place_bjt.setShortcut(QKeySequence("B"))  # Keyboard shortcut for tool selection
        self._placement_action_to_type[self.action_place_bjt] = "BJT"
        self.action_place_bjt.triggered.connect(self.on_place_component_clicked)
        toolbar.addAction(self.action_place_bjt)
        self.btn_place_bjt = toolbar.widgetForAction(self.action_place_bjt)
        
        self.action_place_mosfet = QAction(icon("comp_mosfet.svg"), "MOSFET", self)
        self.action_place_mosfet.setCheckable(True)
        self.action_place_mosfet.setToolTip("MOSFET")
        self.action_place_mosfet.setShortcut(QKeySequence("M"))  # Keyboard shortcut for tool selection
        self._placement_action_to_type[self.action_place_mosfet] = "MOSFET"
        self.action_place_mosfet.triggered.connect(self.on_place_component_clicked)
        toolbar.addAction(self.action_place_mosfet)
        self.btn_place_mosfet = toolbar.widgetForAction(self.action_place_mosfet)
        
        self.action_place_opamp = QAction(icon("comp_opamp.svg"), "Op-amp", self)
        self.action_place_opamp.setCheckable(True)
        self.action_place_opamp.setToolTip("Op-amp")
        self.action_place_opamp.setShortcut(QKeySequence("O"))  # Keyboard shortcut for tool selection
        self._placement_action_to_type[self.action_place_opamp] = "OPAMP"
        self.action_place_opamp.triggered.connect(self.on_place_component_clicked)
        toolbar.addAction(self.action_place_opamp)
        self.btn_place_opamp = toolbar.widgetForAction(self.action_place_opamp)
        
        self.action_place_voltage = QAction(icon("comp_vsource.svg"), "Voltage Source", self)
        self.action_place_voltage.setCheckable(True)
        self.action_place_voltage.setToolTip("Voltage Source")
        self.action_place_voltage.setShortcut(QKeySequence("V"))  # Keyboard shortcut for tool selection
        self._placement_action_to_type[self.action_place_voltage] = "V"
        self.action_place_voltage.triggered.connect(self.on_place_component_clicked)
        toolbar.addAction(self.action_place_voltage)
        self.btn_place_voltage = toolbar.widgetForAction(self.action_place_voltage)
        
        self.action_place_current = QAction(icon("comp_isource.svg"), "Current Source", self)
        self.action_place_current.setCheckable(True)
        self.action_place_current.setToolTip("Current Source")
        self._placement_action_to_type[self.action_place_current] = "I"
        self.action_place_current.triggered.connect(self.on_place_component_clicked)
        toolbar.addAction(self.action_place_current)
        self.btn_place_current = toolbar.widgetForAction(self.action_place_current)
        
        self.action_place_ground = QAction(icon("comp_ground.svg"), "Ground", self)
        self.action_place_ground.setCheckable(True)
        self.action_place_ground.setToolTip("Ground")
        self.action_place_ground.setShortcut(QKeySequence("G"))  # Keyboard shortcut for tool selection
        self._placement_action_to_type[self.action_place_ground] = "GND"
        self.action_place_ground.triggered.connect(self.on_place_component_clicked)
        toolbar.addAction(self.action_place_ground)
        self.btn_place_ground = toolbar.widgetForAction(self.action_place_ground)
        
        self.action_place_net_label = QAction(icon("tool_net_label.svg"), "Net Label", self)
        self.action_place_net_label.setCheckable(True)
        self.action_place_net_label.setToolTip("Net Label")
        self.action_place_net_label.setShortcut(QKeySequence("N"))  # Keyboard shortcut for tool selection
        self.action_place_net_label.triggered.connect(lambda: self._stub_action("Place Net Label")())
        toolbar.addAction(self.action_place_net_label)
        self.btn_place_net_label = toolbar.widgetForAction(self.action_place_net_label)
        
        toolbar.addSeparator()
        
        self.action_rotate = QAction(icon("tool_rotate.svg"), "Rotate", self)
        self.action_rotate.setToolTip("Rotate Component")
        self.action_rotate.triggered.connect(self.on_rotate_component)
        toolbar.addAction(self.action_rotate)
        self.btn_rotate = toolbar.widgetForAction(self.action_rotate)
        
        action_flip = QAction(icon("tool_flip.svg"), "Flip", self)
        action_flip.setToolTip("Flip Component")
        action_flip.triggered.connect(lambda: self._stub_action("Flip component")())
        toolbar.addAction(action_flip)
        
        self.action_delete = QAction(icon("tool_delete.svg"), "Delete", self)
        self.action_delete.setCheckable(True)
        self.action_delete.setToolTip("Delete")
        self.action_delete.triggered.connect(self.on_delete_mode)
        toolbar.addAction(self.action_delete)
        self.btn_delete = toolbar.widgetForAction(self.action_delete)
        
        toolbar.addSeparator()
        
        # Store component placement actions for mode management (keep compatibility with existing code)
        self._placement_buttons = [
            self.btn_place_resistor,
            self.btn_place_capacitor,
            self.btn_place_inductor,
            self.btn_place_diode,
            self.btn_place_bjt,
            self.btn_place_mosfet,
            self.btn_place_opamp,
            self.btn_place_voltage,
            self.btn_place_current,
            self.btn_place_ground,
        ]
        # Also store actions for easier access
        self._placement_actions = [
            self.action_place_resistor,
            self.action_place_capacitor,
            self.action_place_inductor,
            self.action_place_diode,
            self.action_place_bjt,
            self.action_place_mosfet,
            self.action_place_opamp,
            self.action_place_voltage,
            self.action_place_current,
            self.action_place_ground,
        ]
        
        # Simulation & AI controls (right side of toolbar)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel("|"))  # Visual separator
        
        # Keyboard shortcuts for analyses
        self.action_run_dc = QAction(icon("sim_run_dc.svg"), "Run DC", self)
        self.action_run_dc.setToolTip("Run DC Analysis")
        self.action_run_dc.setShortcut(QKeySequence("Ctrl+Alt+D"))  # Keyboard shortcut for analysis
        self.action_run_dc.triggered.connect(self.on_dc_analysis)
        toolbar.addAction(self.action_run_dc)
        
        self.action_run_ac = QAction(icon("sim_run_ac.svg"), "Run AC", self)
        self.action_run_ac.setToolTip("Run AC Analysis")
        self.action_run_ac.setShortcut(QKeySequence("Ctrl+Alt+A"))  # Keyboard shortcut for analysis
        self.action_run_ac.triggered.connect(self.on_ac_analysis)
        toolbar.addAction(self.action_run_ac)
        
        self.action_run_transient = QAction(icon("sim_transient.svg"), "Run Transient", self)
        self.action_run_transient.setToolTip("Run Transient Analysis")
        self.action_run_transient.setShortcut(QKeySequence("Ctrl+Alt+T"))  # Keyboard shortcut for analysis
        self.action_run_transient.triggered.connect(self.on_transient_analysis)
        toolbar.addAction(self.action_run_transient)
        
        self.action_run_noise = QAction(icon("sim_noise.svg"), "Run Noise", self)
        self.action_run_noise.setToolTip("Run Noise Analysis")
        self.action_run_noise.setShortcut(QKeySequence("Ctrl+Alt+N"))  # Keyboard shortcut for analysis
        self.action_run_noise.triggered.connect(self.on_noise_analysis)
        toolbar.addAction(self.action_run_noise)
        
        self.action_run_fft = QAction(icon("sim_fft.svg"), "Run FFT/THD", self)
        self.action_run_fft.setToolTip("Run FFT/THD Analysis")
        self.action_run_fft.setShortcut(QKeySequence("Ctrl+Alt+F"))  # Keyboard shortcut for analysis
        self.action_run_fft.triggered.connect(self.on_fft_analysis)
        toolbar.addAction(self.action_run_fft)
        
        toolbar.addSeparator()
        
        self.action_ai_optimize = QAction(icon("ai_optimize.svg"), "AI Optimize", self)
        self.action_ai_optimize.setToolTip("AI Optimize Circuit")
        self.action_ai_optimize.triggered.connect(lambda: self._stub_action("AI Optimize")())
        toolbar.addAction(self.action_ai_optimize)
        
        self.action_ai_chat = QAction(icon("ai_chat.svg"), "AI Explain", self)
        self.action_ai_chat.setToolTip("AI Explain Circuit")
        self.action_ai_chat.triggered.connect(lambda: self._stub_action("AI Explain Circuit")())
        toolbar.addAction(self.action_ai_chat)
        
        # Add analysis actions to Simulation menu (after they're created)
        # This ensures shortcuts are shown in the menu
        self.sim_menu.addAction(self.action_run_dc)
        self.sim_menu.addAction(self.action_run_ac)
        self.sim_menu.addAction(self.action_run_transient)
        self.sim_menu.addAction(self.action_run_noise)
        self.sim_menu.addAction(self.action_run_fft)
        self.sim_menu.addSeparator()
        self.sim_menu.addAction("Re-simulate Current", self.on_resimulate_current)

    def _setup_central_widget(self):
        """Set SchematicView as the central widget."""
        self.schematic_view = SchematicView()
        self.setCentralWidget(self.schematic_view)
        
        # React to clicks on components in the schematic
        self.schematic_view.componentClicked.connect(self.on_component_clicked)
        # React to selection being cleared
        self.schematic_view.selectionCleared.connect(self._on_selection_cleared)

    def _setup_left_dock(self):
        """Create left dock widget for Component Library."""
        dock = QDockWidget("Component Library", self)
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        
        # Create tree widget for component categories
        tree = QTreeWidget()
        tree.setHeaderLabel("Components")
        tree.setRootIsDecorated(True)
        
        # Passive Components
        passive_item = QTreeWidgetItem(tree, ["Passive Components"])
        tree.addTopLevelItem(passive_item)
        passive_item.addChild(QTreeWidgetItem(["Resistor"]))
        passive_item.addChild(QTreeWidgetItem(["Capacitor"]))
        passive_item.addChild(QTreeWidgetItem(["Inductor"]))
        passive_item.setExpanded(True)
        
        # Semiconductors
        semi_item = QTreeWidgetItem(tree, ["Semiconductors"])
        tree.addTopLevelItem(semi_item)
        semi_item.addChild(QTreeWidgetItem(["Diode"]))
        semi_item.addChild(QTreeWidgetItem(["Zener diode"]))
        semi_item.addChild(QTreeWidgetItem(["BJT (NPN / PNP)"]))
        semi_item.addChild(QTreeWidgetItem(["MOSFET (NMOS / PMOS)"]))
        semi_item.setExpanded(True)
        
        # Sources
        sources_item = QTreeWidgetItem(tree, ["Sources"])
        tree.addTopLevelItem(sources_item)
        sources_item.addChild(QTreeWidgetItem(["Voltage source (DC, AC, Pulse, PWL)"]))
        sources_item.addChild(QTreeWidgetItem(["Current source (DC, AC)"]))
        sources_item.setExpanded(True)
        
        # Controlled Sources
        controlled_item = QTreeWidgetItem(tree, ["Controlled Sources"])
        tree.addTopLevelItem(controlled_item)
        controlled_item.addChild(QTreeWidgetItem(["VCVS (E)"]))
        controlled_item.addChild(QTreeWidgetItem(["VCCS (G)"]))
        controlled_item.addChild(QTreeWidgetItem(["CCVS (H)"]))
        controlled_item.addChild(QTreeWidgetItem(["CCCS (F)"]))
        controlled_item.setExpanded(True)
        
        # Op-amps
        opamp_item = QTreeWidgetItem(tree, ["Op-Amps"])
        tree.addTopLevelItem(opamp_item)
        opamp_item.addChild(QTreeWidgetItem(["Generic op-amp symbol"]))
        opamp_item.addChild(QTreeWidgetItem(["Vendor op-amps placeholder"]))
        opamp_item.setExpanded(True)
        
        # User Macros (future)
        macros_item = QTreeWidgetItem(tree, ["User Macros (future)"])
        tree.addTopLevelItem(macros_item)
        macros_item.addChild(QTreeWidgetItem(["Custom subcircuits"]))
        macros_item.setExpanded(True)
        
        # Connect item selection to component placement
        tree.itemDoubleClicked.connect(self._on_component_library_item_selected)
        
        dock.setWidget(tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        
    def _setup_right_dock(self):
        """Create right dock widget for Properties / Analysis Setup."""
        dock = QDockWidget("Properties / Analysis Setup", self)
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        
        # Create tabbed widget
        tabs = QTabWidget()
        
        # Properties tab
        props_widget = QWidget()
        props_layout = QVBoxLayout(props_widget)
        
        # Properties form for editable fields
        self.properties_form = QFormLayout()
        
        # Reference (read-only label)
        self.prop_ref_label = QLabel("No component selected")
        self.properties_form.addRow("Reference:", self.prop_ref_label)
        
        # Type (read-only label)
        self.prop_type_label = QLabel("—")
        self.properties_form.addRow("Type:", self.prop_type_label)
        
        # Value (editable spinbox)
        self.prop_value_edit = QDoubleSpinBox()
        self.prop_value_edit.setRange(1e-12, 1e12)
        self.prop_value_edit.setDecimals(6)
        self.prop_value_edit.setSuffix("")
        self.prop_value_edit.setEnabled(False)
        self.prop_value_edit.valueChanged.connect(self._on_property_value_changed)
        self.properties_form.addRow("Value:", self.prop_value_edit)
        
        # Net connections (read-only)
        self.prop_nets_label = QLabel("—")
        self.prop_nets_label.setWordWrap(True)
        self.properties_form.addRow("Net connections:", self.prop_nets_label)
        
        # Extra parameters (read-only for now, can be made editable later)
        self.prop_extra_label = QLabel("—")
        self.prop_extra_label.setWordWrap(True)
        self.properties_form.addRow("Extra parameters:", self.prop_extra_label)
        
        props_layout.addLayout(self.properties_form)
        
        # Advanced properties button (optional - for complex edits)
        self.btn_properties_dialog = QPushButton("Advanced Properties...")
        self.btn_properties_dialog.clicked.connect(self._on_open_properties_dialog)
        self.btn_properties_dialog.setEnabled(False)
        props_layout.addWidget(self.btn_properties_dialog)
        
        props_layout.addStretch()
        tabs.addTab(props_widget, "Properties")
        
        # Analysis Setup tab
        analysis_widget = QWidget()
        analysis_layout = QVBoxLayout(analysis_widget)
        
        # Analysis type
        form = QFormLayout()
        analysis_type_combo = QComboBox()
        analysis_type_combo.addItems(["DC", "AC", "Transient", "Noise", "FFT/THD"])
        form.addRow("Analysis Type:", analysis_type_combo)
        
        # Frequency (for AC, FFT)
        self.freq_edit = QDoubleSpinBox()
        self.freq_edit.setRange(1.0, 1e12)
        self.freq_edit.setValue(1000000.0)  # 1 MHz default
        self.freq_edit.setSuffix(" Hz")
        form.addRow("Frequency:", self.freq_edit)
        
        # Time span (for Transient)
        time_span_edit = QDoubleSpinBox()
        time_span_edit.setRange(1e-9, 1.0)
        time_span_edit.setValue(0.001)
        time_span_edit.setSuffix(" s")
        form.addRow("Time Span:", time_span_edit)
        
        analysis_layout.addLayout(form)
        
        # Model file selector (moved from old layout)
        model_group = QGroupBox("Model File")
        model_layout = QVBoxLayout()
        self.model_path_edit = QLineEdit()
        model_layout.addWidget(self.model_path_edit)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.on_browse_model)
        model_layout.addWidget(browse_btn)
        model_group.setLayout(model_layout)
        analysis_layout.addWidget(model_group)
        
        # Target gain (for optimization - moved from old layout)
        opt_group = QGroupBox("Optimization")
        opt_layout = QFormLayout()
        self.target_gain_edit = QLineEdit("40.0")
        opt_layout.addRow("Target Gain (dB):", self.target_gain_edit)
        opt_group.setLayout(opt_layout)
        analysis_layout.addWidget(opt_group)
        
        analysis_layout.addStretch()
        tabs.addTab(analysis_widget, "Analysis Setup")
        
        dock.setWidget(tabs)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    def _setup_bottom_dock(self):
        """Create bottom dock widget for Log / Results."""
        dock = QDockWidget("Log / Results", self)
        dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea)
        
        # Create tabbed widget
        tabs = QTabWidget()
        
        # Simulation Log tab
        sim_log_widget = QWidget()
        sim_log_layout = QVBoxLayout(sim_log_widget)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        sim_log_layout.addWidget(self.output)
        tabs.addTab(sim_log_widget, "Simulation Log")
        
        # AI Log tab
        ai_log_widget = QWidget()
        ai_log_layout = QVBoxLayout(ai_log_widget)
        self.ai_log_output = QTextEdit()
        self.ai_log_output.setReadOnly(True)
        self.ai_log_output.setPlainText("AI Log.\nAI reasoning and suggestions will appear here.")
        ai_log_layout.addWidget(self.ai_log_output)
        tabs.addTab(ai_log_widget, "AI Log")
        
        # Netlist Preview tab
        netlist_widget = QWidget()
        netlist_layout = QVBoxLayout(netlist_widget)
        self.netlist_output = QTextEdit()
        self.netlist_output.setReadOnly(True)
        self.netlist_output.setPlainText("Netlist preview will appear here.")
        netlist_layout.addWidget(self.netlist_output)
        tabs.addTab(netlist_widget, "Netlist Preview")
        
        # Placeholder tabs for plots (stubs for now)
        tabs.addTab(QWidget(), "AC Plot (Bode)")
        tabs.addTab(QWidget(), "Noise Plot")
        tabs.addTab(QWidget(), "Transient Waveform")
        tabs.addTab(QWidget(), "FFT / THD Plot")
        
        dock.setWidget(tabs)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)

    def _on_component_library_item_selected(self, item: QTreeWidgetItem, column: int):
        """Handle component selection from library tree.
        Maps component names to placement types and activates placement mode.
        """
        text = item.text(column)
        if item.parent() is None:  # Skip category items
            return
            
        # Map component library items to placement types
        component_map = {
            "Resistor": "R",
            "Capacitor": "C",
            "Inductor": "L",
            "Diode": "D",
            "Zener diode": "D",  # For now, use D
            "BJT (NPN / PNP)": "BJT",
            "MOSFET (NMOS / PMOS)": "MOSFET",
            "Voltage source (DC, AC, Pulse, PWL)": "V",
            "Current source (DC, AC)": "I",
            "VCVS (E)": "E",
            "VCCS (G)": "G",
            "CCVS (H)": "H",
            "CCCS (F)": "F",
            "Generic op-amp symbol": "OPAMP",
            "Vendor op-amps placeholder": "OPAMP",
        }
        
        component_type = component_map.get(text)
        if component_type:
            # Activate placement mode
            self.log(f"Component library: Selected '{text}' - activating placement mode")
            # Uncheck all placement actions
            for act in self._placement_actions:
                act.setChecked(False)
            self.action_pointer.setChecked(False)
            self.action_wire.setChecked(False)
            
            # Find and activate the corresponding action
            for act, ctype in self._placement_action_to_type.items():
                if ctype == component_type:
                    act.setChecked(True)
                    self.schematic_view.set_placement_mode(component_type)
                    return
            
            # If no action found, activate placement mode directly
            if component_type in ["R", "C", "OPAMP", "V", "I", "GND"]:
                # These have actions, already handled above
                pass
            else:
                # For components without toolbar actions, activate placement mode directly
                self.schematic_view.set_placement_mode(component_type)
                self.log(f"Placement mode activated for {component_type}")
        else:
            self.log(f"Component library: '{text}' selected (placement mode not yet implemented)")

    def _on_open_properties_dialog(self):
        """Open properties dialog for selected component."""
        self._on_edit_properties_from_panel()

    def _stub_action(self, action_name: str):
        """Return a stub slot function for unimplemented features."""
        def stub():
            QMessageBox.information(self, "Not Implemented", f"{action_name} is not implemented yet.")
            print(f"STUB: {action_name}")
        return stub
    


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
        if path and hasattr(self, 'model_path_edit') and self.model_path_edit is not None:
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

        # Get model path
        if not hasattr(self, 'model_path_edit') or self.model_path_edit is None:
            QMessageBox.warning(self, "Error", "GUI controls not initialized. Please restart the application.")
            return
        model_path = self.model_path_edit.text().strip()
        if not model_path:
            QMessageBox.warning(self, "Missing model", "Please select a vendor model (.lib) first.")
            return

        # Get target gain
        try:
            if not hasattr(self, 'target_gain_edit') or self.target_gain_edit is None:
                QMessageBox.warning(self, "Error", "GUI controls not initialized.")
                return
            target_gain_db = float(self.target_gain_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid gain", "Please enter a numeric target gain (dB).")
            return

        # Get frequency from spinbox (QDoubleSpinBox uses .value())
        try:
            if not hasattr(self, 'freq_edit') or self.freq_edit is None:
                QMessageBox.warning(self, "Error", "GUI controls not initialized.")
                return
            if hasattr(self.freq_edit, 'value'):
                freq_hz = self.freq_edit.value()
            else:
                freq_hz = float(self.freq_edit.text())
        except (ValueError, AttributeError):
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
        Updates the properties panel to show component info.
        User can then click "Edit Properties" button to open the dialog.
        """
        # Store selected component ref for rotation and properties
        self.selected_component_ref = ref
        
        # Find component in schematic model
        schematic_comp = None
        if hasattr(self, "schematic_view") and self.schematic_view.model:
            for comp in self.schematic_view.model.components:
                if comp.ref == ref:
                    schematic_comp = comp
                    break

        if schematic_comp is None:
            # Clear properties panel if component not found
            self._clear_properties_panel("Component not found")
            return

        # Update properties panel with component info
        self._update_properties_panel(schematic_comp)
        
        # Enable properties dialog button
        if hasattr(self, "btn_properties_dialog"):
            self.btn_properties_dialog.setEnabled(True)

    def _on_selection_cleared(self):
        """Handle when selection is cleared in schematic view."""
        self.selected_component_ref = None
        self._clear_properties_panel()
    
    def _clear_properties_panel(self, message: str = "No component selected"):
        """Clear the properties panel and show a message."""
        if not hasattr(self, "prop_ref_label"):
            return
        
        self.prop_ref_label.setText(message)
        self.prop_type_label.setText("—")
        self.prop_value_edit.blockSignals(True)
        self.prop_value_edit.setValue(0.0)
        self.prop_value_edit.setEnabled(False)
        self.prop_value_edit.blockSignals(False)
        self.prop_nets_label.setText("—")
        self.prop_extra_label.setText("—")
        if hasattr(self, "btn_properties_dialog"):
            self.btn_properties_dialog.setEnabled(False)
    
    def _on_property_value_changed(self, new_value: float):
        """Handle property value changes from the properties panel."""
        if not self.selected_component_ref:
            return
        
        # Find component in schematic model
        schematic_comp = None
        if hasattr(self, "schematic_view") and self.schematic_view.model:
            for comp in self.schematic_view.model.components:
                if comp.ref == self.selected_component_ref:
                    schematic_comp = comp
                    break
        
        if schematic_comp is None:
            return
        
        # Update the component value
        schematic_comp.value = new_value
        
        # Update the schematic view to reflect the change
        if hasattr(self, "schematic_view"):
            self.schematic_view._redraw_from_model()
        
        # Log the change
        self.log(f"{self.selected_component_ref} value updated to {new_value}")
    
    def _update_properties_panel(self, comp):
        """Update the properties panel with component information."""
        if not hasattr(self, "prop_ref_label"):
            return
        
        # Update reference
        self.prop_ref_label.setText(comp.ref)
        
        # Update type
        self.prop_type_label.setText(comp.ctype)
        
        # Update value (temporarily disable signal to prevent recursive updates)
        self.prop_value_edit.blockSignals(True)
        self.prop_value_edit.setValue(float(comp.value) if comp.value is not None else 0.0)
        
        # Set appropriate suffix and range based on component type
        if comp.ctype == "R":
            # Resistor: ohms
            self.prop_value_edit.setSuffix(" Ω")
            self.prop_value_edit.setRange(1e-6, 1e12)
        elif comp.ctype == "C":
            # Capacitor: farads
            self.prop_value_edit.setSuffix(" F")
            self.prop_value_edit.setRange(1e-15, 1.0)
        elif comp.ctype == "L":
            # Inductor: henries
            self.prop_value_edit.setSuffix(" H")
            self.prop_value_edit.setRange(1e-12, 1.0)
        elif comp.ctype == "V":
            # Voltage source: volts
            self.prop_value_edit.setSuffix(" V")
            self.prop_value_edit.setRange(-1000.0, 1000.0)
        elif comp.ctype == "I":
            # Current source: amperes
            self.prop_value_edit.setSuffix(" A")
            self.prop_value_edit.setRange(-100.0, 100.0)
        else:
            self.prop_value_edit.setSuffix("")
            self.prop_value_edit.setRange(1e-12, 1e12)
        
        self.prop_value_edit.setEnabled(True)
        self.prop_value_edit.blockSignals(False)
        
        # Update net connections
        if comp.pins:
            net_lines = []
            for pin in comp.pins:
                net_name = pin.net if pin.net else "No net"
                net_lines.append(f"Pin {pin.name}: {net_name}")
            self.prop_nets_label.setText("<br>".join(net_lines))
        else:
            self.prop_nets_label.setText("No pins")
        
        # Update extra parameters
        if comp.extra:
            extra_lines = []
            for key, value in comp.extra.items():
                extra_lines.append(f"{key}: {value}")
            self.prop_extra_label.setText("<br>".join(extra_lines))
        else:
            self.prop_extra_label.setText("None")
        
    def _on_edit_properties_from_panel(self):
        """Open properties dialog for the currently selected component."""
        if not self.selected_component_ref:
            QMessageBox.information(self, "No Selection", "Please select a component first.")
            return
            
        # Find component
        schematic_comp = None
        if hasattr(self, "schematic_view") and self.schematic_view.model:
            for comp in self.schematic_view.model.components:
                if comp.ref == self.selected_component_ref:
                    schematic_comp = comp
                    break
        
        if schematic_comp is None:
            QMessageBox.warning(self, "Not found", f"Component {self.selected_component_ref} not found.")
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
                if getattr(comp, "ref", "") == self.selected_component_ref:
                    circuit_comp = comp
                    break
            
            if circuit_comp and "value" in properties:
                circuit_comp.value = properties["value"]
                # Update schematic labels from circuit
                self._update_schematic_from_circuit(self.current_circuit)

        # Update properties panel and redraw schematic
        self._update_properties_panel(schematic_comp)
        if hasattr(self, "schematic_view"):
            self.schematic_view._redraw_from_model()
        
        # Log the change
        prop_str = ", ".join(f"{k}={v}" for k, v in properties.items())
        self.log(f"{self.selected_component_ref} properties updated: {prop_str}. "
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
        self.action_pointer.setChecked(select_active)
        self.action_wire.setChecked(not select_active)

    def on_mode_select(self):
        self._update_mode_buttons(True)
        self.schematic_view.set_mode("select")

    def on_mode_wire(self):
        self._update_mode_buttons(False)
        self._clear_placement_buttons()
        self.schematic_view.set_mode("wire")

    def on_place_component_clicked(self):
        """Handle component placement action trigger."""
        sender_action = self.sender()
        if sender_action is None:
            return
        
        component_type = self._placement_action_to_type.get(sender_action)
        if component_type is None:
            return
        
        # Uncheck all other placement actions
        for action in self._placement_actions:
            if action != sender_action:
                action.setChecked(False)
        
        # Uncheck mode actions
        self.action_pointer.setChecked(False)
        self.action_wire.setChecked(False)
        
        # Set placement mode in schematic view
        if sender_action.isChecked():
            self.schematic_view.set_placement_mode(component_type)
        else:
            # If unchecking, go back to select mode
            self.action_pointer.setChecked(True)
            self.schematic_view.set_mode("select")

    def _setup_keyboard_shortcuts(self):
        """Setup keyboard shortcuts for component placement and modes."""
        # Note: Tool selection shortcuts are now set directly on QActions using setShortcut()
        # This method remains for Delete/Escape shortcuts and any future non-action shortcuts
        
        # Delete/Escape to reset mode
        QShortcut(QKeySequence("Delete"), self, activated=self._reset_mode)
        QShortcut(QKeySequence("Escape"), self, activated=self._reset_mode)
        
        # Keyboard shortcut for current source (not in rules.md but useful to keep)
        if hasattr(self, 'action_place_current'):
            self.action_place_current.setShortcut(QKeySequence("I"))
    
    def _place_component_by_key(self, component_type: str):
        """Place a component using keyboard shortcut."""
        # Find the corresponding action
        action = None
        for act, ctype in self._placement_action_to_type.items():
            if ctype == component_type:
                action = act
                break
        
        if action:
            # Uncheck all other actions first
            for act in self._placement_actions:
                if act != action:
                    act.setChecked(False)
            self.action_pointer.setChecked(False)
            self.action_wire.setChecked(False)
            
            # Toggle the action (this will trigger on_place_component_clicked)
            action.setChecked(True)
            action.trigger()
            
            # Focus the schematic view so clicks work
            self.schematic_view.setFocus()
    
    def _activate_wire_mode(self):
        """Activate wire mode using keyboard shortcut."""
        # Uncheck all placement actions
        for act in self._placement_actions:
            act.setChecked(False)
        self.action_pointer.setChecked(False)
        
        # Activate wire mode
        self.action_wire.setChecked(True)
        self.action_wire.trigger()
        
        # Focus the schematic view
        self.schematic_view.setFocus()
    
    def _reset_mode(self):
        """Reset to select mode (unselect current mode)."""
        # Uncheck all placement actions
        for act in self._placement_actions:
            act.setChecked(False)
        self.action_delete.setChecked(False)
        
        # Uncheck mode actions
        self.action_wire.setChecked(False)
        
        # Activate select mode
        self.action_pointer.setChecked(True)
        if hasattr(self, 'schematic_view'):
            self.schematic_view.set_mode("select")
        
        # Focus the schematic view
        if hasattr(self, 'schematic_view'):
            self.schematic_view.setFocus()

    def on_delete_mode(self):
        """Handle delete tool action trigger."""
        if self.sender().isChecked():
            # Uncheck all other actions
            for act in self._placement_actions:
                act.setChecked(False)
            self.action_pointer.setChecked(False)
            self.action_wire.setChecked(False)
            self.schematic_view.set_mode("delete")
        else:
            # If unchecking, go back to select mode
            self.action_pointer.setChecked(True)
            self.schematic_view.set_mode("select")

    def _clear_placement_buttons(self):
        """Uncheck all placement actions."""
        for act in self._placement_actions:
            act.setChecked(False)
        self.action_delete.setChecked(False)

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
    
    def on_ac_analysis(self) -> None:
        """Run AC analysis on the current schematic."""
        QMessageBox.information(self, "Not Implemented", "Run AC analysis is not implemented yet.")
        print("TODO: Run AC analysis")
    
    def on_transient_analysis(self) -> None:
        """Run Transient analysis on the current schematic."""
        QMessageBox.information(self, "Not Implemented", "Run Transient analysis is not implemented yet.")
        print("TODO: Run Transient analysis")
    
    def on_noise_analysis(self) -> None:
        """Run Noise analysis on the current schematic."""
        QMessageBox.information(self, "Not Implemented", "Run Noise analysis is not implemented yet.")
        print("TODO: Run Noise analysis")
    
    def on_fft_analysis(self) -> None:
        """Run FFT/THD analysis on the current schematic."""
        QMessageBox.information(self, "Not Implemented", "Run FFT/THD analysis is not implemented yet.")
        print("TODO: Run FFT/THD analysis")


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(900, 600)
    win.show()
    sys.exit(app.exec())
    

if __name__ == "__main__":
    main()
