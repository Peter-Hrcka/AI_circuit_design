"""
Component Properties Dialog

A comprehensive dialog for editing component properties based on component type.
"""

from __future__ import annotations
from typing import Optional, Dict, Any
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QComboBox,
    QFormLayout,
    QDialogButtonBox,
    QMessageBox,
    QCheckBox,
)

from core.schematic_model import SchematicComponent
from core.model_analyzer import analyze_model


class ComponentPropertiesDialog(QDialog):
    """
    Dialog for editing component properties.
    
    Displays different fields based on component type:
    - Resistors: value
    - Capacitors: value, tolerance, ESR
    - Op-amps: model file, supply rails
    - Voltage sources: DC level, AC amplitude
    - Transistors: model selection, parameters
    """
    
    def __init__(self, component: SchematicComponent, parent=None):
        super().__init__(parent)
        self.component = component
        self.result_properties: Dict[str, Any] = {}
        
        self.setWindowTitle(f"Properties: {component.ref}")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        
        # Create form based on component type
        self.form_widgets = {}
        
        if component.ctype == "R":
            self._create_resistor_form(layout)
        elif component.ctype == "C":
            self._create_capacitor_form(layout)
        elif component.ctype == "L":
            self._create_inductor_form(layout)
        elif component.ctype == "D":
            self._create_diode_form(layout)
        elif component.ctype == "Q":
            self._create_bjt_form(layout)
        elif component.ctype == "M" or component.ctype == "M_bulk":
            self._create_mosfet_form(layout)
        elif component.ctype == "G":
            self._create_vccs_form(layout)
        elif component.ctype == "OPAMP" or component.ctype == "OPAMP_ideal":
            self._create_opamp_form(layout)
        elif component.ctype == "V":
            self._create_voltage_source_form(layout)
        elif component.ctype == "I":
            self._create_current_source_form(layout)
        else:
            self._create_generic_form(layout)
        
        # Dialog buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        # Load current values
        self._load_current_values()
    
    def _create_resistor_form(self, layout: QVBoxLayout):
        """Create form for resistor properties."""
        form_layout = QFormLayout()
        
        # Value
        value_spin = QDoubleSpinBox()
        value_spin.setRange(0.0, 1e12)
        value_spin.setDecimals(3)
        value_spin.setSuffix(" Ω")
        value_spin.setValue(self.component.value)
        form_layout.addRow("Value:", value_spin)
        self.form_widgets["value"] = value_spin
        
        group = QGroupBox("Resistor Properties")
        group.setLayout(form_layout)
        layout.addWidget(group)
    
    def _create_capacitor_form(self, layout: QVBoxLayout):
        """Create form for capacitor properties."""
        form_layout = QFormLayout()
        
        # Value
        value_spin = QDoubleSpinBox()
        value_spin.setRange(0.0, 1.0)
        value_spin.setDecimals(12)
        value_spin.setSuffix(" F")
        value_spin.setValue(self.component.value)
        form_layout.addRow("Value (Farads):", value_spin)
        self.form_widgets["value"] = value_spin
        
        # Tolerance (%)
        tolerance_spin = QDoubleSpinBox()
        tolerance_spin.setRange(0.0, 100.0)
        tolerance_spin.setDecimals(2)
        tolerance_spin.setSuffix(" %")
        form_layout.addRow("Tolerance:", tolerance_spin)
        self.form_widgets["tolerance"] = tolerance_spin
        
        # ESR (Equivalent Series Resistance)
        esr_spin = QDoubleSpinBox()
        esr_spin.setRange(0.0, 1e6)
        esr_spin.setDecimals(6)
        esr_spin.setSuffix(" Ω")
        form_layout.addRow("ESR:", esr_spin)
        self.form_widgets["esr"] = esr_spin
        
        group = QGroupBox("Capacitor Properties")
        group.setLayout(form_layout)
        layout.addWidget(group)
    
    def _create_opamp_form(self, layout: QVBoxLayout):
        """Create form for op-amp properties."""
        form_layout = QFormLayout()
        
        # Model file
        model_layout = QHBoxLayout()
        model_path_edit = QLineEdit()
        model_path_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(lambda: self._browse_model_file(model_path_edit))
        
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: model_path_edit.clear())
        
        model_layout.addWidget(model_path_edit)
        model_layout.addWidget(browse_btn)
        model_layout.addWidget(clear_btn)
        form_layout.addRow("Model File:", model_layout)
        self.form_widgets["model_file"] = model_path_edit
        
        # Subckt name
        subckt_layout = QHBoxLayout()
        subckt_edit = QLineEdit()
        auto_detect_btn = QPushButton("Auto-detect")
        auto_detect_btn.clicked.connect(lambda: self._auto_detect_opamp_subckt(model_path_edit, subckt_edit))
        
        subckt_layout.addWidget(subckt_edit)
        subckt_layout.addWidget(auto_detect_btn)
        form_layout.addRow("Subckt name:", subckt_layout)
        self.form_widgets["subckt_name"] = subckt_edit
        
        # PSpice compatibility checkbox
        pspice_compat_checkbox = QCheckBox("Enable PSpice compatibility for ngspice")
        pspice_compat_checkbox.setToolTip("Enables ngspice PSpice/PS behavior mode for vendor models that use PSpice-like expressions.")
        form_layout.addRow("", pspice_compat_checkbox)
        self.form_widgets["ngspice_pspice_compat"] = pspice_compat_checkbox
        
        group = QGroupBox("Op-Amp Properties")
        group.setLayout(form_layout)
        layout.addWidget(group)
    
    def _create_voltage_source_form(self, layout: QVBoxLayout):
        """Create form for voltage source properties."""
        form_layout = QFormLayout()
        
        # DC Level
        dc_spin = QDoubleSpinBox()
        dc_spin.setRange(-1e6, 1e6)
        dc_spin.setDecimals(3)
        dc_spin.setSuffix(" V")
        dc_spin.setValue(self.component.value)  # DC level is typically stored in value
        form_layout.addRow("DC Level:", dc_spin)
        self.form_widgets["dc_level"] = dc_spin
        
        # AC Amplitude
        ac_spin = QDoubleSpinBox()
        ac_spin.setRange(0.0, 1e6)
        ac_spin.setDecimals(3)
        ac_spin.setSuffix(" V")
        form_layout.addRow("AC Amplitude:", ac_spin)
        self.form_widgets["ac_amplitude"] = ac_spin
        
        group = QGroupBox("Voltage Source Properties")
        group.setLayout(form_layout)
        layout.addWidget(group)
    
    def _create_current_source_form(self, layout: QVBoxLayout):
        """Create form for current source properties."""
        form_layout = QFormLayout()
        
        # Current value
        value_spin = QDoubleSpinBox()
        value_spin.setRange(-1e6, 1e6)
        value_spin.setDecimals(3)
        value_spin.setSuffix(" A")
        value_spin.setValue(self.component.value)
        form_layout.addRow("Current:", value_spin)
        self.form_widgets["value"] = value_spin
        
        group = QGroupBox("Current Source Properties")
        group.setLayout(form_layout)
        layout.addWidget(group)
    
    def _create_inductor_form(self, layout: QVBoxLayout):
        """Create form for inductor properties."""
        form_layout = QFormLayout()
        
        # Inductance value
        value_spin = QDoubleSpinBox()
        value_spin.setRange(0.0, 1e6)
        value_spin.setDecimals(9)
        value_spin.setSuffix(" H")
        value_spin.setValue(self.component.value)
        form_layout.addRow("Inductance:", value_spin)
        self.form_widgets["value"] = value_spin
        
        group = QGroupBox("Inductor Properties")
        group.setLayout(form_layout)
        layout.addWidget(group)
    
    def _create_diode_form(self, layout: QVBoxLayout):
        """Create form for diode properties."""
        form_layout = QFormLayout()
        
        # Model name
        model_edit = QLineEdit()
        model_edit.setText(str(self.component.extra.get("model", "")))
        form_layout.addRow("SPICE Model:", model_edit)
        self.form_widgets["model"] = model_edit
        
        group = QGroupBox("Diode Properties")
        group.setLayout(form_layout)
        layout.addWidget(group)
    
    def _create_bjt_form(self, layout: QVBoxLayout):
        """Create form for BJT transistor properties."""
        form_layout = QFormLayout()
        
        # Polarity (NPN/PNP)
        polarity_combo = QComboBox()
        polarity_combo.addItems(["NPN", "PNP"])
        current_polarity = str(self.component.extra.get("polarity", "NPN")).upper()
        polarity_combo.setCurrentText(current_polarity if current_polarity in ("NPN", "PNP") else "NPN")
        form_layout.addRow("Polarity:", polarity_combo)
        self.form_widgets["polarity"] = polarity_combo
        
        # Model name
        model_edit = QLineEdit()
        model_edit.setText(str(self.component.extra.get("model", "")))
        form_layout.addRow("SPICE Model:", model_edit)
        self.form_widgets["model"] = model_edit
        
        group = QGroupBox("BJT Properties")
        group.setLayout(form_layout)
        layout.addWidget(group)
    
    def _create_mosfet_form(self, layout: QVBoxLayout):
        """Create form for MOSFET transistor properties."""
        form_layout = QFormLayout()
        
        # MOSFET type (NMOS/PMOS)
        mos_type_combo = QComboBox()
        mos_type_combo.addItems(["NMOS", "PMOS"])
        current_type = str(self.component.extra.get("mos_type", "NMOS")).upper()
        mos_type_combo.setCurrentText(current_type if current_type in ("NMOS", "PMOS") else "NMOS")
        form_layout.addRow("Type:", mos_type_combo)
        self.form_widgets["mos_type"] = mos_type_combo
        
        # Model file
        model_file_layout = QHBoxLayout()
        model_file_edit = QLineEdit()
        model_file_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(lambda: self._browse_mosfet_model_file(model_file_edit))
        
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: model_file_edit.clear())
        
        model_file_layout.addWidget(model_file_edit)
        model_file_layout.addWidget(browse_btn)
        model_file_layout.addWidget(clear_btn)
        form_layout.addRow("Model File:", model_file_layout)
        self.form_widgets["model_file"] = model_file_edit
        
        # Model name
        model_layout = QHBoxLayout()
        model_edit = QLineEdit()
        model_edit.setText(str(self.component.extra.get("model", "")))
        auto_detect_btn = QPushButton("Auto-detect")
        auto_detect_btn.clicked.connect(lambda: self._auto_detect_mosfet_model(model_file_edit, model_edit))
        
        model_layout.addWidget(model_edit)
        model_layout.addWidget(auto_detect_btn)
        form_layout.addRow("SPICE Model:", model_layout)
        self.form_widgets["model"] = model_edit
        
        # PSpice compatibility checkbox
        pspice_compat_checkbox = QCheckBox("Enable PSpice compatibility for ngspice")
        pspice_compat_checkbox.setToolTip("Enables ngspice PSpice/PS behavior mode for vendor models that use PSpice-like expressions.")
        form_layout.addRow("", pspice_compat_checkbox)
        self.form_widgets["ngspice_pspice_compat"] = pspice_compat_checkbox
        
        group = QGroupBox("MOSFET Properties")
        group.setLayout(form_layout)
        layout.addWidget(group)
    
    def _create_vccs_form(self, layout: QVBoxLayout):
        """Create form for voltage-controlled current source (VCCS) properties."""
        form_layout = QFormLayout()
        
        # Transconductance
        value_spin = QDoubleSpinBox()
        value_spin.setRange(-1e6, 1e6)
        value_spin.setDecimals(6)
        value_spin.setSuffix(" S")
        value_spin.setValue(self.component.value)
        form_layout.addRow("Transconductance:", value_spin)
        self.form_widgets["value"] = value_spin
        
        group = QGroupBox("VCCS Properties")
        group.setLayout(form_layout)
        layout.addWidget(group)
    
    def _create_generic_form(self, layout: QVBoxLayout):
        """Create generic form for unknown component types."""
        form_layout = QFormLayout()
        
        value_spin = QDoubleSpinBox()
        value_spin.setRange(-1e12, 1e12)
        value_spin.setDecimals(3)
        value_spin.setValue(self.component.value)
        form_layout.addRow("Value:", value_spin)
        self.form_widgets["value"] = value_spin
        
        group = QGroupBox(f"{self.component.ctype} Properties")
        group.setLayout(form_layout)
        layout.addWidget(group)
    
    def _browse_model_file(self, path_edit: QLineEdit):
        """Open file dialog to select op-amp model file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Op-Amp Model File",
            "",
            "SPICE Models (*.lib *.cir *.sub *.sp *.spi *.model);;All Files (*.*)"
        )
        if file_path:
            path_edit.setText(file_path)
    
    def _browse_mosfet_model_file(self, path_edit: QLineEdit):
        """Open file dialog to select MOSFET model file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select MOSFET Model File",
            "",
            "SPICE Models (*.lib *.cir *.sub *.sp *.spi *.model);;All Files (*.*)"
        )
        if file_path:
            path_edit.setText(file_path)
    
    def _auto_detect_opamp_subckt(self, model_file_edit: QLineEdit, subckt_edit: QLineEdit):
        """Auto-detect subckt name from model file."""
        model_file = model_file_edit.text().strip()
        if not model_file:
            QMessageBox.warning(
                self,
                "No Model File",
                "Please select a model file first."
            )
            return
        
        try:
            meta = analyze_model(model_file)
            if meta.model_names:
                subckt_edit.setText(meta.model_names[0])
            else:
                QMessageBox.information(
                    self,
                    "No Subckt Found",
                    "No subckt names were found in the model file."
                )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to analyze model file:\n{str(e)}"
            )
    
    def _auto_detect_mosfet_model(self, model_file_edit: QLineEdit, model_edit: QLineEdit):
        """Auto-detect model name from model file."""
        model_file = model_file_edit.text().strip()
        if not model_file:
            QMessageBox.warning(
                self,
                "No Model File",
                "Please select a model file first."
            )
            return
        
        try:
            meta = analyze_model(model_file)
            if meta.model_names:
                model_edit.setText(meta.model_names[0])
            else:
                QMessageBox.information(
                    self,
                    "No Model Found",
                    "No model names were found in the model file."
                )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to analyze model file:\n{str(e)}"
            )
    
    def _load_current_values(self):
        """Load current component values into form widgets."""
        extra = self.component.extra
        
        if self.component.ctype == "C":
            # Load capacitor properties
            if "tolerance" in extra:
                self.form_widgets["tolerance"].setValue(float(extra["tolerance"]))
            if "esr" in extra:
                self.form_widgets["esr"].setValue(float(extra["esr"]))
        
        elif self.component.ctype == "OPAMP" or self.component.ctype == "OPAMP_ideal":
            # Load op-amp properties
            if "model_file" in extra:
                self.form_widgets["model_file"].setText(str(extra["model_file"]))
            if "subckt_name" in extra:
                self.form_widgets["subckt_name"].setText(str(extra["subckt_name"]))
            if "ngspice_pspice_compat" in self.form_widgets:
                self.form_widgets["ngspice_pspice_compat"].setChecked(extra.get("ngspice_pspice_compat", False))
        
        elif self.component.ctype == "V":
            # Load voltage source properties
            if "dc_level" in extra:
                self.form_widgets["dc_level"].setValue(float(extra["dc_level"]))
            elif self.component.value != 0.0:
                self.form_widgets["dc_level"].setValue(self.component.value)
            if "ac_amplitude" in extra:
                self.form_widgets["ac_amplitude"].setValue(float(extra["ac_amplitude"]))
        
        elif self.component.ctype == "D":
            # Load diode properties
            if "model" in self.form_widgets and "model" in extra:
                self.form_widgets["model"].setText(str(extra["model"]))
        
        elif self.component.ctype == "Q":
            # Load BJT properties
            if "polarity" in self.form_widgets:
                polarity = str(extra.get("polarity", "NPN")).upper()
                if polarity in ("NPN", "PNP"):
                    self.form_widgets["polarity"].setCurrentText(polarity)
            if "model" in self.form_widgets and "model" in extra:
                self.form_widgets["model"].setText(str(extra["model"]))
        
        elif self.component.ctype == "M" or self.component.ctype == "M_bulk":
            # Load MOSFET properties
            if "mos_type" in self.form_widgets:
                mos_type = str(extra.get("mos_type", "NMOS")).upper()
                if mos_type in ("NMOS", "PMOS"):
                    self.form_widgets["mos_type"].setCurrentText(mos_type)
            if "model_file" in extra:
                self.form_widgets["model_file"].setText(str(extra["model_file"]))
            if "model" in self.form_widgets and "model" in extra:
                self.form_widgets["model"].setText(str(extra["model"]))
            if "ngspice_pspice_compat" in self.form_widgets:
                self.form_widgets["ngspice_pspice_compat"].setChecked(extra.get("ngspice_pspice_compat", False))
    
    def accept(self):
        """Collect values from form and store in result_properties."""
        # Always update value
        if "value" in self.form_widgets:
            self.result_properties["value"] = self.form_widgets["value"].value()
        
        if self.component.ctype == "C":
            # Collect capacitor properties
            self.result_properties["tolerance"] = self.form_widgets["tolerance"].value()
            self.result_properties["esr"] = self.form_widgets["esr"].value()
        
        elif self.component.ctype == "OPAMP" or self.component.ctype == "OPAMP_ideal":
            # Collect op-amp properties
            model_file = self.form_widgets["model_file"].text().strip()
            # Always include model_file (even if empty) so we can clear it
            self.result_properties["model_file"] = model_file if model_file else None
            subckt_name = self.form_widgets["subckt_name"].text().strip()
            self.result_properties["subckt_name"] = subckt_name if subckt_name else None
            if "ngspice_pspice_compat" in self.form_widgets:
                self.result_properties["ngspice_pspice_compat"] = self.form_widgets["ngspice_pspice_compat"].isChecked()
        
        elif self.component.ctype == "V":
            # Collect voltage source properties
            self.result_properties["dc_level"] = self.form_widgets["dc_level"].value()
            self.result_properties["ac_amplitude"] = self.form_widgets["ac_amplitude"].value()
            # Also update value to DC level
            self.result_properties["value"] = self.result_properties["dc_level"]
        
        elif self.component.ctype == "I":
            # Current source: value already handled above
            pass
        
        elif self.component.ctype == "L":
            # Inductor: value already handled above
            pass
        
        elif self.component.ctype == "D":
            # Collect diode properties
            if "model" in self.form_widgets:
                model_name = self.form_widgets["model"].text().strip()
                if model_name:
                    self.result_properties["model"] = model_name
                elif "model" in self.component.extra:
                    # Clear model if field is empty
                    self.result_properties["model"] = None
        
        elif self.component.ctype == "Q":
            # Collect BJT properties
            if "polarity" in self.form_widgets:
                self.result_properties["polarity"] = self.form_widgets["polarity"].currentText()
            if "model" in self.form_widgets:
                model_name = self.form_widgets["model"].text().strip()
                if model_name:
                    self.result_properties["model"] = model_name
                elif "model" in self.component.extra:
                    self.result_properties["model"] = None
        
        elif self.component.ctype == "M" or self.component.ctype == "M_bulk":
            # Collect MOSFET properties
            if "mos_type" in self.form_widgets:
                self.result_properties["mos_type"] = self.form_widgets["mos_type"].currentText()
            model_file = self.form_widgets["model_file"].text().strip()
            self.result_properties["model_file"] = model_file if model_file else None
            if "model" in self.form_widgets:
                model_name = self.form_widgets["model"].text().strip()
                if model_name:
                    self.result_properties["model"] = model_name
                elif "model" in self.component.extra:
                    self.result_properties["model"] = None
            if "ngspice_pspice_compat" in self.form_widgets:
                self.result_properties["ngspice_pspice_compat"] = self.form_widgets["ngspice_pspice_compat"].isChecked()
        
        elif self.component.ctype == "G":
            # VCCS: value (transconductance) already handled above
            pass
        
        super().accept()

