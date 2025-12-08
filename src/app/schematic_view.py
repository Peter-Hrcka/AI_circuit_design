# src/app/schematic_view.py

from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsLineItem, QGraphicsEllipseItem, QGraphicsTextItem, QGraphicsPolygonItem, QGraphicsRectItem
from PySide6.QtGui import QPen, QPainter, QBrush, QColor, QPolygonF
from PySide6.QtCore import Qt, QRectF, QPointF, Signal

from core.schematic_model import (
    SchematicModel,
    SchematicComponent,
    SchematicPin,
    SchematicWire,
    SchematicJunction,
)
from core.net_extraction import (
    extract_nets_with_intersections,
    validate_all_pins_have_nets,
    find_line_intersection,
)
from core.wire_utils import find_nearest_wire


class SchematicView(QGraphicsView):
    """
    Schematic canvas with:
      - ability to render a SchematicModel (components + wires)
      - select mode: click components to edit (via componentClicked)
      - wire mode: click two pins to create a SchematicWire
        (right-click on a wire deletes it)
    """

    # Emitted when user clicks a component body (e.g. "R1", "R2", "Rin")
    componentClicked = Signal(str)
    # Emitted when selection is cleared (clicked empty space)
    selectionCleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        scene = QGraphicsScene(self)
        self.setScene(scene)

        self.setRenderHint(QPainter.Antialiasing)
        self.setMouseTracking(True)

        self._pen = QPen(Qt.GlobalColor.black)
        self._pen.setWidthF(1.4)

        # Pens and brushes for visual elements
        self._pin_pen = QPen(Qt.GlobalColor.blue, 2.0)
        self._pin_brush = QBrush(Qt.GlobalColor.blue)
        self._hover_pen = QPen(Qt.GlobalColor.red, 2.5)
        self._hover_brush = QBrush(Qt.GlobalColor.red)
        self._junction_pen = QPen(Qt.GlobalColor.black)
        self._junction_brush = QBrush(Qt.GlobalColor.black)
        self._preview_pen = QPen(Qt.GlobalColor.gray, 1.5, Qt.PenStyle.DashLine)
        self._grid_pen = QPen(QColor(220, 220, 220), 1.0)  # Light grey grid
        self._selection_pen = QPen(QColor(0, 120, 215), 2.5, Qt.PenStyle.DashLine)  # Blue dashed for selection

        # Map QGraphicsItem -> ref name ("Rin", "R1", "R2")
        self._component_items: dict = {}
        # Map QGraphicsItem -> SchematicComponent (for dragging)
        self._component_graphics_to_model: dict = {}
        # Map ref name -> QGraphicsItem (for reverse lookup)
        self._component_ref_to_graphics: dict = {}
        # Map QGraphicsItem -> original pen (for hover restoration)
        self._component_original_pens: dict = {}
        
        # Grid settings
        self._grid_size = 10.0  # pixels
        self._snap_to_grid_enabled = True
        self._show_grid = True  # Show grid background
        
        # Wire drawing state (for new click-to-place wire mode)
        self._wire_start_pos: tuple[float, float] | None = None  # (x, y) where wire starts

        # Map QGraphicsItem (line) -> SchematicWire (for deletion)
        self._wire_items: dict = {}

        # Map QGraphicsItem -> net label text item
        self._net_label_items: dict = {}
        # Store DC analysis nodal voltages: net_name -> voltage (V)
        self._dc_voltages: dict[str, float] = {}

        # Map QGraphicsItem -> junction dot item
        self._junction_dot_items: dict = {}

        # Map QGraphicsItem -> pin marker item
        self._pin_marker_items: dict = {}

        # Model + interaction state - start with empty schematic
        from core.schematic_model import SchematicModel
        self.model: SchematicModel = SchematicModel()  # Start with empty model
        self._mode: str = "select"      # "select", "wire", "place", or "delete"
        self._placement_type: str | None = None  # Component type to place ("R", "C", "OPAMP", "V", "GND")
        self._pending_pin: SchematicPin | None = None
        self._pending_wire: SchematicWire | None = None  # For wire-to-wire connections
        self._pending_junction_pos: tuple[float, float] | None = None  # (x, y) position for junction
        
        # Component reference counter for generating unique refs
        self._component_ref_counters = {
            "R": 0,
            "C": 0,
            "OPAMP": 0,
            "V": 0,
            "I": 0,
            "GND": 0,
            "VOUT": 0,
        }
        
        # Set drag mode based on initial mode
        self._update_drag_mode()

        # Auto net name generator for wires created by the user
        self._next_auto_net_id: int = 1

        # Hover state
        self._hovered_component_item = None
        self._hovered_pin = None

        # Preview wire (temporary wire while dragging) - list for Manhattan routing segments
        self._preview_wire_item = []
        self._preview_wire_start = None
        
        # Component dragging state
        self._dragging_component = None
        self._drag_start_pos = None
        self._component_initial_pos = None
        self._initial_pin_positions = []
        self._clicked_component_ref = None
        self._drag_threshold = 5.0  # pixels - minimum movement to consider it a drag
        self._is_dragging = False  # Track if dragging has actually started
        
        # Multi-selection state
        self._selected_components: set[str] = set()  # Set of component refs that are selected
        self._selected_components_initial_positions: dict[str, tuple[float, float]] = {}  # Initial positions for all selected components
        self._selected_components_initial_pins: dict[str, list[tuple[float, float]]] = {}  # Initial pin positions for all selected components
        self._selected_components_initial_wires: dict[str, list[tuple[float, float, float, float]]] = {}  # Initial wire endpoints for wires connected to selected components
        
        # Track last-known pin positions during component dragging
        # Key: (component_ref, pin_index) tuple for hashable key
        self._pin_last_positions: dict[tuple[str, int], tuple[float, float]] = {}
        
        # Selection rectangle (marquee selection) state
        self._selection_rect_start: QPointF | None = None  # Start point of selection rectangle
        self._selection_rect_item: QGraphicsRectItem | None = None  # Graphics item for selection rectangle
        self._selection_rect_pen = QPen(QColor(0, 120, 215), 1.5, Qt.PenStyle.DashLine)  # Blue dashed for selection rectangle
        self._selection_rect_brush = QBrush(QColor(0, 120, 215, 30))  # Semi-transparent blue fill

        # Preview component state (for component placement preview)
        self._preview_component_items = []
        self._preview_component_pen = QPen(QColor(128, 128, 128), 1.5, Qt.PenStyle.DashLine)

        # Draw from model so pins + dots + wire bookkeeping are all set up
        self._redraw_from_model()

    def _update_drag_mode(self):
        """Update the drag mode based on current mode.
        
        We implement our own selection rectangle and component dragging,
        so we MUST NOT use QGraphicsView's RubberBandDrag, otherwise Qt
        will draw its own selection rectangle even while we're moving
        components.
        """
        # Always disable QGraphicsView's built-in drag selection
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def set_mode(self, mode: str):
        """Set the interaction mode: 'select', 'wire', 'place', or 'delete'."""
        self._mode = mode
        self._placement_type = None
        self._pending_pin = None
        self._pending_wire = None
        self._pending_junction_pos = None
        self._wire_start_pos = None
        self._clear_preview_component()
        # Clear preview wire when switching modes to prevent leftover preview from appearing
        self._clear_preview_wire(reset_start=True)
        self._update_drag_mode()

    def set_placement_mode(self, component_type: str):
        """Set mode to place a specific component type."""
        self._mode = "place"
        self._placement_type = component_type
        self._update_drag_mode()

    def set_dc_voltages(self, nodal_voltages: dict[str, float]):
        """Set DC analysis nodal voltages for display on schematic."""
        print(f"DEBUG: set_dc_voltages called with {len(nodal_voltages)} voltages")
        self._dc_voltages = {}
        for net_name, voltage in nodal_voltages.items():
            net_lower = net_name.lower()
            self._dc_voltages[net_lower] = voltage
            print(f"DEBUG: Storing net {net_lower}: {voltage:.3f}V")
        self._redraw_from_model()

    def clear_dc_voltages(self):
        """Clear DC analysis voltages from display."""
        self._dc_voltages = {}
        self._redraw_from_model()

    def rotate_component(self, ref: str) -> bool:
        """Rotate a component by 90 degrees. Returns True if successful."""
        for comp in self.model.components:
            if comp.ref == ref:
                comp.rotation = (comp.rotation + 90) % 360
                self._redraw_from_model()
                return True
        return False

    def set_component_values(self, rin=None, r1=None, r2=None):
        """Legacy method for setting component values (for backward compatibility)."""
        # This is a legacy method - components should be updated via the model directly
        pass

    def sync_values_from_circuit(self, circuit):
        """Sync component values from a Circuit object to the schematic model."""
        # This method can be implemented if needed for backward compatibility
        pass

    def _snap_to_grid(self, x: float, y: float) -> tuple[float, float]:
        """Snap coordinates to grid."""
        if not self._snap_to_grid_enabled:
            return (x, y)
        grid = self._grid_size
        return (round(x / grid) * grid, round(y / grid) * grid)

    def _draw_grid(self):
        """Draw grid background."""
        if not self._show_grid:
            return

        scene = self.scene()
        scene_rect = scene.sceneRect()
        grid = self._grid_size
        
        # Draw vertical lines
        x = scene_rect.left()
        while x <= scene_rect.right():
            line = QGraphicsLineItem(x, scene_rect.top(), x, scene_rect.bottom())
            line.setPen(self._grid_pen)
            line.setZValue(-1000)  # Behind everything
            scene.addItem(line)
            x += grid
        
        # Draw horizontal lines
        y = scene_rect.top()
        while y <= scene_rect.bottom():
            line = QGraphicsLineItem(scene_rect.left(), y, scene_rect.right(), y)
            line.setPen(self._grid_pen)
            line.setZValue(-1000)  # Behind everything
            scene.addItem(line)
            y += grid

    def _redraw_from_model(self):
        """Clear and redraw everything from the schematic model."""
        scene = self.scene()
        
        # Clear preview items list before clearing scene (scene.clear() will delete them)
        self._preview_component_items.clear()
        # Clear preview wire item reference before clearing scene
        self._preview_wire_item = []
        # Clear selection rectangle item reference before clearing scene
        self._selection_rect_item = None
        
        scene.clear()
        
        # Clear all tracking dictionaries
        self._component_items.clear()
        self._component_graphics_to_model.clear()
        self._component_ref_to_graphics.clear()
        self._component_original_pens.clear()
        self._wire_items.clear()
        self._net_label_items.clear()
        self._junction_dot_items.clear()
        self._pin_marker_items.clear()
        
        # Set scene rect
        scene.setSceneRect(-2000, -2000, 4000, 4000)
        
        # Draw grid
        self._draw_grid()
        
        if self.model is None:
            return

        # Draw all components
        for comp in self.model.components:
            self._draw_component(comp)
        
        # Draw all wires
        for wire in self.model.wires:
            self._draw_wire(wire)
        
        # Extract nets and add labels
        extract_nets_with_intersections(self.model)
        self._add_net_labels()
        
        # Draw selected components with selection highlight
        for ref in self._selected_components:
            comp_item = self._component_ref_to_graphics.get(ref)
            if comp_item:
                # Draw selection rectangle around component
                comp = self._get_component_by_ref(ref)
                if comp:
                    bbox = self._get_component_bounding_box(comp)
                    if bbox:
                        min_x, min_y, max_x, max_y = bbox
                        rect_item = QGraphicsRectItem(min_x - 3, min_y - 3, max_x - min_x + 6, max_y - min_y + 6)
                        rect_item.setPen(self._selection_pen)
                        rect_item.setZValue(1000)  # Above everything
                        scene.addItem(rect_item)

    def _draw_component(self, comp: SchematicComponent):
        """Draw a single component."""
        if comp.ctype == "R":
            self._draw_resistor(comp)
        elif comp.ctype == "C":
            self._draw_capacitor(comp)
        elif comp.ctype == "OPAMP":
            self._draw_opamp(comp)
        elif comp.ctype == "V":
            self._draw_voltage_source(comp)
        elif comp.ctype == "I":
            self._draw_current_source(comp)
        elif comp.ctype == "GND":
            self._draw_ground(comp)
        elif comp.ctype == "VOUT":
            self._draw_vout(comp)

    def _get_component_by_ref(self, ref: str) -> SchematicComponent | None:
        """Get a component by its reference."""
        for comp in self.model.components:
            if comp.ref == ref:
                return comp
        return None

    def _get_component_bounding_box(self, comp: SchematicComponent) -> tuple[float, float, float, float] | None:
        """Get bounding box for a component for collision detection."""
        if comp is None:
            return None
        
        if comp.ctype == "R":
            # Resistor: fixed body length 50.0, height 16.0
            body_length = 50.0
            body_height = 16.0
            padding = 1.0
            if len(comp.pins) >= 2:
                pin1_x, pin1_y = comp.pins[0].x, comp.pins[0].y
                pin2_x, pin2_y = comp.pins[1].x, comp.pins[1].y
                # Calculate center
                center_x = (pin1_x + pin2_x) / 2
                center_y = (pin1_y + pin2_y) / 2
                # Calculate body bounds
                dx = pin2_x - pin1_x
                dy = pin2_y - pin1_y
                length = (dx**2 + dy**2)**0.5
                if length > 0:
                    cos_theta = dx / length
                    sin_theta = dy / length
                    half_body = body_length / 2
                    half_height = body_height / 2
                    body_min_x = center_x - half_body * abs(cos_theta) - half_height * abs(sin_theta)
                    body_max_x = center_x + half_body * abs(cos_theta) + half_height * abs(sin_theta)
                    body_min_y = center_y - half_body * abs(sin_theta) - half_height * abs(cos_theta)
                    body_max_y = center_y + half_body * abs(sin_theta) + half_height * abs(cos_theta)
                    return (body_min_x - padding, body_min_y - padding, body_max_x + padding, body_max_y + padding)
        elif comp.ctype == "C":
            # Capacitor: similar to resistor
            body_length = 30.0
            body_height = 20.0
            padding = 1.0
            if len(comp.pins) >= 2:
                pin1_x, pin1_y = comp.pins[0].x, comp.pins[0].y
                pin2_x, pin2_y = comp.pins[1].x, comp.pins[1].y
                center_x = (pin1_x + pin2_x) / 2
                center_y = (pin1_y + pin2_y) / 2
                dx = pin2_x - pin1_x
                dy = pin2_y - pin1_y
                length = (dx**2 + dy**2)**0.5
                if length > 0:
                    cos_theta = dx / length
                    sin_theta = dy / length
                    half_body = body_length / 2
                    half_height = body_height / 2
                    body_min_x = center_x - half_body * abs(cos_theta) - half_height * abs(sin_theta)
                    body_max_x = center_x + half_body * abs(cos_theta) + half_height * abs(sin_theta)
                    body_min_y = center_y - half_body * abs(sin_theta) - half_height * abs(cos_theta)
                    body_max_y = center_y + half_body * abs(sin_theta) + half_height * abs(cos_theta)
                    return (body_min_x - padding, body_min_y - padding, body_max_x + padding, body_max_y + padding)
        elif comp.ctype == "V" or comp.ctype == "I":
            # Voltage/Current source: circle with radius ~15, plus short wires
            padding = 2.0
            if len(comp.pins) >= 2:
                pin1_x, pin1_y = comp.pins[0].x, comp.pins[0].y
                pin2_x, pin2_y = comp.pins[1].x, comp.pins[1].y
                center_x = (pin1_x + pin2_x) / 2
                center_y = (pin1_y + pin2_y) / 2
                radius = 15.0
                wire_length = 10.0  # Short wire length
                # Bounding box includes circle and wires
                min_x = min(pin1_x, pin2_x, center_x - radius) - wire_length - padding
                max_x = max(pin1_x, pin2_x, center_x + radius) + wire_length + padding
                min_y = min(pin1_y, pin2_y, center_y - radius) - wire_length - padding
                max_y = max(pin1_y, pin2_y, center_y + radius) + wire_length + padding
                return (min_x, min_y, max_x, max_y)
        elif comp.ctype == "GND":
            # Ground: small symbol, use pin position with small padding
            padding = 5.0
            if len(comp.pins) >= 1:
                pin_x, pin_y = comp.pins[0].x, comp.pins[0].y
                return (pin_x - padding, pin_y - padding, pin_x + padding, pin_y + padding)
        elif comp.ctype == "OPAMP":
            # Op-amp: triangle, use all pin positions
            padding = 3.0
            if len(comp.pins) >= 3:
                xs = [p.x for p in comp.pins]
                ys = [p.y for p in comp.pins]
                return (min(xs) - padding, min(ys) - padding, max(xs) + padding, max(ys) + padding)
        
        # Fallback: use pin positions
        if comp.pins:
            xs = [p.x for p in comp.pins]
            ys = [p.y for p in comp.pins]
            padding = 5.0
            return (min(xs) - padding, min(ys) - padding, max(xs) + padding, max(ys) + padding)
        
        return None

    def _check_component_overlap(self, comp: SchematicComponent) -> bool:
        """Check if a component overlaps with existing components."""
        bbox = self._get_component_bounding_box(comp)
        if bbox is None:
            return False
        
        min_x, min_y, max_x, max_y = bbox
        
        for existing_comp in self.model.components:
            if existing_comp.ref == comp.ref:
                continue  # Skip self
            existing_bbox = self._get_component_bounding_box(existing_comp)
            if existing_bbox is None:
                continue
            ex_min_x, ex_min_y, ex_max_x, ex_max_y = existing_bbox
            
            # Check for overlap
            if not (max_x < ex_min_x or min_x > ex_max_x or max_y < ex_min_y or min_y > ex_max_y):
                return True  # Overlap detected
        
        return False

    def _update_wires_connected_to_pin(self, old_x: float, old_y: float, new_x: float, new_y: float):
        """Update wires that are connected to a pin that has moved.
        
        Args:
            old_x, old_y: Original pin position
            new_x, new_y: New pin position
        """
        # Tolerance for matching wire endpoints to pin positions
        tolerance = 1.0
        
        for wire in self.model.wires:
            # Check if wire endpoint 1 matches the old pin position
            if abs(wire.x1 - old_x) < tolerance and abs(wire.y1 - old_y) < tolerance:
                wire.x1 = new_x
                wire.y1 = new_y
            # Check if wire endpoint 2 matches the old pin position
            if abs(wire.x2 - old_x) < tolerance and abs(wire.y2 - old_y) < tolerance:
                wire.x2 = new_x
                wire.y2 = new_y

    def _generate_component_ref(self, component_type: str) -> str:
        """Generate a unique component reference."""
        prefix_map = {
            "R": "R", "C": "C", "OPAMP": "U", "V": "V", "I": "I", "GND": "GND", "VOUT": "VOUT",
        }
        prefix = prefix_map.get(component_type, "X")
        self._component_ref_counters[component_type] += 1
        num = self._component_ref_counters[component_type]
        return f"{prefix}{num}"

    def _create_resistor(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create a resistor component at position (x, y)."""
        half_len = 25.0
        pin_offset = 15.0
        pin1_x, pin1_y = self._snap_to_grid(x - half_len - pin_offset, y)
        pin2_x, pin2_y = self._snap_to_grid(x + half_len + pin_offset, y)
        
        comp = SchematicComponent(
            ref=ref,
            ctype="R",
            x=x,
            y=y,
            rotation=0,
            value=10000.0,
            pins=[
                SchematicPin(name="1", x=pin1_x, y=pin1_y, net=None),
                SchematicPin(name="2", x=pin2_x, y=pin2_y, net=None),
            ],
        )
        return comp

    def _create_capacitor(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create a capacitor component at position (x, y)."""
        pin_offset = 15.0
        pin1_x, pin1_y = self._snap_to_grid(x - pin_offset, y)
        pin2_x, pin2_y = self._snap_to_grid(x + pin_offset, y)
        
        comp = SchematicComponent(
            ref=ref,
            ctype="C",
            x=x,
            y=y,
            rotation=0,
            value=1e-6,
            pins=[
                SchematicPin(name="1", x=pin1_x, y=pin1_y, net=None),
                SchematicPin(name="2", x=pin2_x, y=pin2_y, net=None),
            ],
        )
        return comp

    def _create_opamp(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create an op-amp component at position (x, y)."""
        size = 40.0
        comp = SchematicComponent(
            ref=ref,
            ctype="OPAMP",
            x=x,
            y=y,
            rotation=0,
            value=0.0,
            pins=[
                SchematicPin(name="+", x=x, y=y - size/2, net=None),  # Non-inverting
                SchematicPin(name="-", x=x, y=y + size/2, net=None),  # Inverting
                SchematicPin(name="out", x=x + size, y=y, net=None),  # Output
            ],
        )
        return comp

    def _create_voltage_source(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create a voltage source component at position (x, y)."""
        wire_length = 10.0  # Short wire length
        pin1_x, pin1_y = self._snap_to_grid(x, y - wire_length)
        pin2_x, pin2_y = self._snap_to_grid(x, y + wire_length)
        
        comp = SchematicComponent(
            ref=ref,
            ctype="V",
            x=x,
            y=y,
            rotation=0,
            value=5.0,
            pins=[
                SchematicPin(name="+", x=pin1_x, y=pin1_y, net=None),
                SchematicPin(name="-", x=pin2_x, y=pin2_y, net=None),
            ],
        )
        return comp

    def _create_current_source(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create a current source component at position (x, y)."""
        wire_length = 10.0  # Short wire length
        pin1_x, pin1_y = self._snap_to_grid(x, y - wire_length)
        pin2_x, pin2_y = self._snap_to_grid(x, y + wire_length)
        
        comp = SchematicComponent(
            ref=ref,
            ctype="I",
            x=x,
            y=y,
            rotation=0,
            value=1.0,  # 1A default
            pins=[
                SchematicPin(name="+", x=pin1_x, y=pin1_y, net=None),
                SchematicPin(name="-", x=pin2_x, y=pin2_y, net=None),
            ],
        )
        return comp

    def _create_ground(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create a ground component at position (x, y)."""
        pin_x, pin_y = self._snap_to_grid(x, y)
        
        comp = SchematicComponent(
            ref=ref,
            ctype="GND",
            x=x,
            y=y,
            rotation=0,
            value=0.0,
            pins=[
                SchematicPin(name="gnd", x=pin_x, y=pin_y, net="0"),
            ],
        )
        return comp

    def _create_vout(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create a VOUT marker at position (x, y)."""
        pin_x, pin_y = self._snap_to_grid(x, y)
        
        comp = SchematicComponent(
            ref=ref,
            ctype="VOUT",
            x=x,
            y=y,
            rotation=0,
            value=0.0,
            pins=[
                SchematicPin(name="out", x=pin_x, y=pin_y, net=None),
            ],
        )
        return comp

    def _place_component_at(self, x: float, y: float, component_type: str):
        """Place a component at the given position."""
        if component_type is None:
            return
        
        ref = self._generate_component_ref(component_type)
        
        if component_type == "R":
            comp = self._create_resistor(ref, x, y)
        elif component_type == "C":
            comp = self._create_capacitor(ref, x, y)
        elif component_type == "OPAMP":
            comp = self._create_opamp(ref, x, y)
        elif component_type == "V":
            comp = self._create_voltage_source(ref, x, y)
        elif component_type == "I":
            comp = self._create_current_source(ref, x, y)
        elif component_type == "GND":
            comp = self._create_ground(ref, x, y)
        elif component_type == "VOUT":
            comp = self._create_vout(ref, x, y)
        else:
            return
        
        # Check for overlap
        if self._check_component_overlap(comp):
            return  # Don't place if overlapping
        
        self.model.components.append(comp)
        self._redraw_from_model()
        self._clear_preview_component()

    def _draw_resistor(self, comp: SchematicComponent):
        """Draw a resistor component."""
        if len(comp.pins) < 2:
            return
        
        pin1_x, pin1_y = comp.pins[0].x, comp.pins[0].y
        pin2_x, pin2_y = comp.pins[1].x, comp.pins[1].y
        
        # Fixed body length
        body_length = 50.0
        body_height = 16.0
        
        # Calculate center and orientation
        center_x = (pin1_x + pin2_x) / 2
        center_y = (pin1_y + pin2_y) / 2
        dx = pin2_x - pin1_x
        dy = pin2_y - pin1_y
        length = (dx**2 + dy**2)**0.5
        
        if length == 0:
            return
        
        cos_theta = dx / length
        sin_theta = dy / length
        
        # Draw body (European style: rectangle)
        half_len = body_length / 2
        half_height = body_height / 2
        
        # Calculate rectangle corners in local coordinates
        corners_local = [
            (-half_len, -half_height),  # Top-left
            (half_len, -half_height),   # Top-right
            (half_len, half_height),    # Bottom-right
            (-half_len, half_height),   # Bottom-left
        ]
        
        # Transform corners to world coordinates
        corners_world = []
        for x_local, y_local in corners_local:
            x_world = center_x + x_local * cos_theta - y_local * sin_theta
            y_world = center_y + x_local * sin_theta + y_local * cos_theta
            corners_world.append(QPointF(x_world, y_world))
        
        # Draw rectangle using polygon
        scene = self.scene()
        rect_polygon = QPolygonF(corners_world)
        rect_item = QGraphicsPolygonItem(rect_polygon)
        rect_item.setPen(self._pen)
        rect_item.setBrush(QBrush(Qt.GlobalColor.white))
        scene.addItem(rect_item)
        
        # Calculate body end points (where wires connect)
        body_end1_x = center_x - half_len * cos_theta
        body_end1_y = center_y - half_len * sin_theta
        body_end2_x = center_x + half_len * cos_theta
        body_end2_y = center_y + half_len * sin_theta
        
        # Draw short wires from body to pins
        wire1 = QGraphicsLineItem(body_end1_x, body_end1_y, pin1_x, pin1_y)
        wire1.setPen(self._pen)
        scene.addItem(wire1)
        
        wire2 = QGraphicsLineItem(body_end2_x, body_end2_y, pin2_x, pin2_y)
        wire2.setPen(self._pen)
        scene.addItem(wire2)
        
        # Draw pins
        for pin in comp.pins:
            pin_item = QGraphicsEllipseItem(pin.x - 2, pin.y - 2, 4, 4)
            pin_item.setPen(self._pin_pen)
            pin_item.setBrush(self._pin_brush)
            scene.addItem(pin_item)
            self._pin_marker_items[pin_item] = pin
        
        # Draw label
        label = QGraphicsTextItem(comp.ref)
        label.setPos(center_x - 10, center_y - 20)
        scene.addItem(label)
        
        # Store component item
        comp_item = QGraphicsRectItem(center_x - half_len, center_y - half_height, body_length, body_height)
        self._component_items[comp_item] = comp.ref
        self._component_graphics_to_model[comp_item] = comp
        self._component_ref_to_graphics[comp.ref] = comp_item

    def _draw_capacitor(self, comp: SchematicComponent):
        """Draw a capacitor component."""
        if len(comp.pins) < 2:
            return
        
        pin1_x, pin1_y = comp.pins[0].x, comp.pins[0].y
        pin2_x, pin2_y = comp.pins[1].x, comp.pins[1].y
        
        # Calculate center and orientation
        center_x = (pin1_x + pin2_x) / 2
        center_y = (pin1_y + pin2_y) / 2
        dx = pin2_x - pin1_x
        dy = pin2_y - pin1_y
        length = (dx**2 + dy**2)**0.5
        
        if length == 0:
            return
        
        cos_theta = dx / length
        sin_theta = dy / length
        
        # Draw plates (two parallel lines)
        plate_length = 20.0
        plate_spacing = 10.0
        
        # Left plate
        left_x = center_x - plate_spacing/2 * cos_theta
        left_y = center_y - plate_spacing/2 * sin_theta
        p1_x = left_x - plate_length/2 * sin_theta
        p1_y = left_y + plate_length/2 * cos_theta
        p2_x = left_x + plate_length/2 * sin_theta
        p2_y = left_y - plate_length/2 * cos_theta
        
        scene = self.scene()
        line1 = QGraphicsLineItem(p1_x, p1_y, p2_x, p2_y)
        line1.setPen(self._pen)
        scene.addItem(line1)
        
        # Right plate
        right_x = center_x + plate_spacing/2 * cos_theta
        right_y = center_y + plate_spacing/2 * sin_theta
        p3_x = right_x - plate_length/2 * sin_theta
        p3_y = right_y + plate_length/2 * cos_theta
        p4_x = right_x + plate_length/2 * sin_theta
        p4_y = right_y - plate_length/2 * cos_theta
        
        line2 = QGraphicsLineItem(p3_x, p3_y, p4_x, p4_y)
        line2.setPen(self._pen)
        scene.addItem(line2)
        
        # Draw short wires from plates to pins
        # Left plate center connects to pin1
        wire1 = QGraphicsLineItem(left_x, left_y, pin1_x, pin1_y)
        wire1.setPen(self._pen)
        scene.addItem(wire1)
        
        # Right plate center connects to pin2
        wire2 = QGraphicsLineItem(right_x, right_y, pin2_x, pin2_y)
        wire2.setPen(self._pen)
        scene.addItem(wire2)
        
        # Draw pins
        for pin in comp.pins:
            pin_item = QGraphicsEllipseItem(pin.x - 2, pin.y - 2, 4, 4)
            pin_item.setPen(self._pin_pen)
            pin_item.setBrush(self._pin_brush)
            scene.addItem(pin_item)
            self._pin_marker_items[pin_item] = pin
        
        # Draw label
        label = QGraphicsTextItem(comp.ref)
        label.setPos(center_x - 10, center_y - 20)
        scene.addItem(label)
        
        # Store component item
        comp_item = QGraphicsRectItem(center_x - 20, center_y - 10, 40, 20)
        self._component_items[comp_item] = comp.ref
        self._component_graphics_to_model[comp_item] = comp
        self._component_ref_to_graphics[comp.ref] = comp_item

    def _draw_opamp(self, comp: SchematicComponent):
        """Draw an op-amp component (triangle)."""
        if len(comp.pins) < 3:
                    return

        # Find pins
        noninv_pin = comp.pins[0]  # Non-inverting
        inv_pin = comp.pins[1]  # Inverting
        out_pin = comp.pins[2]  # Output
        
        # Calculate triangle vertices
        center_x = (noninv_pin.x + inv_pin.x) / 2
        center_y = (noninv_pin.y + inv_pin.y) / 2
        
        size = 40.0
        tip_x = center_x + size
        tip_y = center_y
        
        # Left vertices
        left_x = center_x - size/2
        top_y = center_y - size/2
        bottom_y = center_y + size/2
        
        scene = self.scene()
        
        # Draw triangle
        triangle = QPolygonF([
            QPointF(tip_x, tip_y),
            QPointF(left_x, top_y),
            QPointF(left_x, bottom_y),
        ])
        triangle_item = QGraphicsPolygonItem(triangle)
        triangle_item.setPen(self._pen)
        triangle_item.setBrush(QBrush(Qt.GlobalColor.white))
        scene.addItem(triangle_item)
        
        # Draw pins
        for pin in comp.pins:
            pin_item = QGraphicsEllipseItem(pin.x - 2, pin.y - 2, 4, 4)
            pin_item.setPen(self._pin_pen)
            pin_item.setBrush(self._pin_brush)
            scene.addItem(pin_item)
            self._pin_marker_items[pin_item] = pin
        
        # Draw labels
        label = QGraphicsTextItem(comp.ref)
        label.setPos(center_x - 10, center_y - 30)
        scene.addItem(label)
        
        # Store component item
        comp_item = QGraphicsRectItem(left_x - 5, top_y - 5, size + 10, size + 10)
        self._component_items[comp_item] = comp.ref
        self._component_graphics_to_model[comp_item] = comp
        self._component_ref_to_graphics[comp.ref] = comp_item

    def _draw_voltage_source(self, comp: SchematicComponent):
        """Draw a voltage source component (circle with + and -)."""
        if len(comp.pins) < 2:
            return
        
        pin1_x, pin1_y = comp.pins[0].x, comp.pins[0].y
        pin2_x, pin2_y = comp.pins[1].x, comp.pins[1].y
        
        center_x = (pin1_x + pin2_x) / 2
        center_y = (pin1_y + pin2_y) / 2
        radius = 15.0
        wire_length = 10.0
        
        scene = self.scene()
        
        # Draw short wires
        dx = pin2_x - pin1_x
        dy = pin2_y - pin1_y
        length = (dx**2 + dy**2)**0.5
        if length > 0:
            cos_theta = dx / length
            sin_theta = dy / length
            # Wire from pin1 to circle
            wire1_end_x = center_x - radius * cos_theta
            wire1_end_y = center_y - radius * sin_theta
            wire1 = QGraphicsLineItem(pin1_x, pin1_y, wire1_end_x, wire1_end_y)
            wire1.setPen(self._pen)
            scene.addItem(wire1)
            # Wire from circle to pin2
            wire2_start_x = center_x + radius * cos_theta
            wire2_start_y = center_y + radius * sin_theta
            wire2 = QGraphicsLineItem(wire2_start_x, wire2_start_y, pin2_x, pin2_y)
            wire2.setPen(self._pen)
            scene.addItem(wire2)
        
        # Draw circle
        circle = QGraphicsEllipseItem(center_x - radius, center_y - radius, radius * 2, radius * 2)
        circle.setPen(self._pen)
        circle.setBrush(QBrush(Qt.GlobalColor.white))
        scene.addItem(circle)
        
        # Draw + and - signs
        plus_size = 6.0
        plus = QGraphicsLineItem(center_x - plus_size/2, center_y, center_x + plus_size/2, center_y)
        plus.setPen(self._pen)
        scene.addItem(plus)
        plus_v = QGraphicsLineItem(center_x, center_y - plus_size/2, center_x, center_y + plus_size/2)
        plus_v.setPen(self._pen)
        scene.addItem(plus_v)
        
        minus = QGraphicsLineItem(center_x - plus_size/2, center_y + radius/2, center_x + plus_size/2, center_y + radius/2)
        minus.setPen(self._pen)
        scene.addItem(minus)
        
        # Draw pins
        for pin in comp.pins:
            pin_item = QGraphicsEllipseItem(pin.x - 2, pin.y - 2, 4, 4)
            pin_item.setPen(self._pin_pen)
            pin_item.setBrush(self._pin_brush)
            scene.addItem(pin_item)
            self._pin_marker_items[pin_item] = pin
        
        # Draw label
        label = QGraphicsTextItem(comp.ref)
        label.setPos(center_x - 10, center_y - 30)
        scene.addItem(label)
        
        # Store component item
        comp_item = QGraphicsEllipseItem(center_x - radius - wire_length, center_y - radius - wire_length, 
                                       (radius + wire_length) * 2, (radius + wire_length) * 2)
        self._component_items[comp_item] = comp.ref
        self._component_graphics_to_model[comp_item] = comp
        self._component_ref_to_graphics[comp.ref] = comp_item

    def _draw_current_source(self, comp: SchematicComponent):
        """Draw a current source component (circle with arrow)."""
        if len(comp.pins) < 2:
            return
        
        pin1_x, pin1_y = comp.pins[0].x, comp.pins[0].y
        pin2_x, pin2_y = comp.pins[1].x, comp.pins[1].y
        
        center_x = (pin1_x + pin2_x) / 2
        center_y = (pin1_y + pin2_y) / 2
        radius = 15.0
        wire_length = 10.0
        
        scene = self.scene()
        
        # Draw short wires
        dx = pin2_x - pin1_x
        dy = pin2_y - pin1_y
        length = (dx**2 + dy**2)**0.5
        if length > 0:
            cos_theta = dx / length
            sin_theta = dy / length
            # Wire from pin1 to circle
            wire1_end_x = center_x - radius * cos_theta
            wire1_end_y = center_y - radius * sin_theta
            wire1 = QGraphicsLineItem(pin1_x, pin1_y, wire1_end_x, wire1_end_y)
            wire1.setPen(self._pen)
            scene.addItem(wire1)
            # Wire from circle to pin2
            wire2_start_x = center_x + radius * cos_theta
            wire2_start_y = center_y + radius * sin_theta
            wire2 = QGraphicsLineItem(wire2_start_x, wire2_start_y, pin2_x, pin2_y)
            wire2.setPen(self._pen)
            scene.addItem(wire2)
        
        # Draw circle
        circle = QGraphicsEllipseItem(center_x - radius, center_y - radius, radius * 2, radius * 2)
        circle.setPen(self._pen)
        circle.setBrush(QBrush(Qt.GlobalColor.white))
        scene.addItem(circle)
        
        # Draw arrow inside circle
        arrow_size = 8.0
        arrow_x = center_x
        arrow_y = center_y
        arrow_tip_x = arrow_x + arrow_size
        arrow_tip_y = arrow_y
        arrow_left_x = arrow_x - arrow_size/2
        arrow_left_y = arrow_y - arrow_size/3
        arrow_right_x = arrow_x - arrow_size/2
        arrow_right_y = arrow_y + arrow_size/3
        
        # Arrow line
        arrow_line = QGraphicsLineItem(arrow_left_x, arrow_y, arrow_tip_x, arrow_tip_y)
        arrow_line.setPen(self._pen)
        scene.addItem(arrow_line)
        
        # Arrow head (triangle)
        arrow_head = QPolygonF([
            QPointF(arrow_tip_x, arrow_tip_y),
            QPointF(arrow_left_x, arrow_left_y),
            QPointF(arrow_right_x, arrow_right_y),
        ])
        arrow_head_item = QGraphicsPolygonItem(arrow_head)
        arrow_head_item.setPen(self._pen)
        arrow_head_item.setBrush(QBrush(Qt.GlobalColor.black))
        scene.addItem(arrow_head_item)
        
        # Draw pins
        for pin in comp.pins:
            pin_item = QGraphicsEllipseItem(pin.x - 2, pin.y - 2, 4, 4)
            pin_item.setPen(self._pin_pen)
            pin_item.setBrush(self._pin_brush)
            scene.addItem(pin_item)
            self._pin_marker_items[pin_item] = pin
        
        # Draw label
        label = QGraphicsTextItem(comp.ref)
        label.setPos(center_x - 10, center_y - 30)
        scene.addItem(label)
        
        # Store component item
        comp_item = QGraphicsEllipseItem(center_x - radius - wire_length, center_y - radius - wire_length, 
                                       (radius + wire_length) * 2, (radius + wire_length) * 2)
        self._component_items[comp_item] = comp.ref
        self._component_graphics_to_model[comp_item] = comp
        self._component_ref_to_graphics[comp.ref] = comp_item

    def _draw_ground(self, comp: SchematicComponent):
        """Draw a ground component."""
        if len(comp.pins) < 1:
            return
        
        pin_x, pin_y = comp.pins[0].x, comp.pins[0].y
        
        scene = self.scene()
        
        # Draw ground symbol (horizontal lines)
        line_length = 12.0
        spacing = 4.0
        
        line1 = QGraphicsLineItem(pin_x - line_length/2, pin_y, pin_x + line_length/2, pin_y)
        line1.setPen(self._pen)
        scene.addItem(line1)
        
        line2 = QGraphicsLineItem(pin_x - line_length/2 + spacing, pin_y + spacing, 
                                 pin_x + line_length/2 - spacing, pin_y + spacing)
        line2.setPen(self._pen)
        scene.addItem(line2)
        
        line3 = QGraphicsLineItem(pin_x - line_length/2 + spacing*2, pin_y + spacing*2, 
                                 pin_x + line_length/2 - spacing*2, pin_y + spacing*2)
        line3.setPen(self._pen)
        scene.addItem(line3)
        
        # Draw pin
        pin_item = QGraphicsEllipseItem(pin_x - 2, pin_y - 2, 4, 4)
        pin_item.setPen(self._pin_pen)
        pin_item.setBrush(self._pin_brush)
        scene.addItem(pin_item)
        self._pin_marker_items[pin_item] = comp.pins[0]
        
        # Draw label
        label = QGraphicsTextItem(comp.ref)
        label.setPos(pin_x - 10, pin_y - 25)
        scene.addItem(label)
        
        # Store component item
        comp_item = QGraphicsRectItem(pin_x - 10, pin_y - 5, 20, 15)
        self._component_items[comp_item] = comp.ref
        self._component_graphics_to_model[comp_item] = comp
        self._component_ref_to_graphics[comp.ref] = comp_item

    def _draw_vout(self, comp: SchematicComponent):
        """Draw a VOUT marker."""
        if len(comp.pins) < 1:
            return
        
        pin_x, pin_y = comp.pins[0].x, comp.pins[0].y
        
        scene = self.scene()
        
        # Draw pin
        pin_item = QGraphicsEllipseItem(pin_x - 2, pin_y - 2, 4, 4)
        pin_item.setPen(self._pin_pen)
        pin_item.setBrush(self._pin_brush)
        scene.addItem(pin_item)
        self._pin_marker_items[pin_item] = comp.pins[0]
        
        # Draw label
        label = QGraphicsTextItem(comp.ref)
        label.setPos(pin_x - 10, pin_y - 25)
        scene.addItem(label)
        
        # Store component item
        comp_item = QGraphicsRectItem(pin_x - 5, pin_y - 5, 10, 10)
        self._component_items[comp_item] = comp.ref
        self._component_graphics_to_model[comp_item] = comp
        self._component_ref_to_graphics[comp.ref] = comp_item

    def _draw_wire(self, wire: SchematicWire):
        """Draw a wire with Manhattan routing (horizontal then vertical)."""
        scene = self.scene()
        
        # Calculate Manhattan route: horizontal first, then vertical
        # Choose the route that minimizes total length
        dx = abs(wire.x2 - wire.x1)
        dy = abs(wire.y2 - wire.y1)
        
        if dx > dy:
            # Horizontal first, then vertical
            mid_x = wire.x2
            mid_y = wire.y1
        else:
            # Vertical first, then horizontal
            mid_x = wire.x1
            mid_y = wire.y2
        
        # Draw first segment (horizontal or vertical)
        line1 = QGraphicsLineItem(wire.x1, wire.y1, mid_x, mid_y)
        line1.setPen(self._pen)
        scene.addItem(line1)
        self._wire_items[line1] = wire
        
        # Draw second segment
        line2 = QGraphicsLineItem(mid_x, mid_y, wire.x2, wire.y2)
        line2.setPen(self._pen)
        scene.addItem(line2)
        self._wire_items[line2] = wire

    def _add_net_labels(self):
        """Add net labels to the schematic."""
        # Group pins by net
        net_to_pins = {}
        for comp in self.model.components:
            for pin in comp.pins:
                if pin.net:
                    net_name = pin.net
                    if net_name not in net_to_pins:
                        net_to_pins[net_name] = []
                    net_to_pins[net_name].append((pin.x, pin.y))
        
        scene = self.scene()
        
        # Add labels for each net (at first pin position)
        for net_name, pin_positions in net_to_pins.items():
            if pin_positions:
                x, y = pin_positions[0]
                
                # Check if voltage is available
                voltage_str = ""
                if net_name and net_name.lower() in self._dc_voltages:
                    voltage = self._dc_voltages[net_name.lower()]
                    if abs(voltage) < 1e-6:
                        voltage_str = "0.000V"
                    elif abs(voltage) < 1:
                        voltage_str = f"{voltage*1000:.3f}mV"
                    else:
                        voltage_str = f"{voltage:.3f}V"
                    label_text = f"{voltage_str}\n{net_name}"
                else:
                    label_text = net_name
                    # Debug logging
                    if net_name and len(self._dc_voltages) > 0:
                        print(f"DEBUG: Net '{net_name}' not found in _dc_voltages. Available keys: {list(self._dc_voltages.keys())}")
                
                label = QGraphicsTextItem(label_text)
                label.setPos(x + 5, y - 10)
                scene.addItem(label)
                self._net_label_items[label] = net_name

    def _clear_preview_component(self):
        """Clear preview component graphics."""
        scene = self.scene()
        # Only remove items that still exist (haven't been deleted by scene.clear())
        items_to_remove = []
        for item in self._preview_component_items:
            try:
                # Check if item still exists by accessing its scene
                if item.scene() is not None:
                    scene.removeItem(item)
            except RuntimeError:
                # Item already deleted, skip it
                pass
        self._preview_component_items.clear()

    def _clear_preview_wire(self, reset_start=False):
        """Clear preview wire graphics and optionally reset wire start position.
        
        Args:
            reset_start: If True, also reset _preview_wire_start to None.
                        If False, only clear the graphics items (for use during mouse move).
        """
        scene = self.scene()
        # Clear preview wire items
        if self._preview_wire_item:
            for item in self._preview_wire_item:
                try:
                    # Check if item still exists by accessing its scene
                    if item.scene() is not None:
                        scene.removeItem(item)
                except RuntimeError:
                    # Item already deleted, skip it
                    pass
            self._preview_wire_item.clear()
        # Reset wire start position only if requested
        if reset_start:
            self._preview_wire_start = None

    def _update_preview_component(self, x: float, y: float):
        """Update preview component at position (x, y)."""
        self._clear_preview_component()
        
        if self._placement_type is None:
                    return

        snapped_x, snapped_y = self._snap_to_grid(x, y)
        
        if self._placement_type == "R":
            self._draw_preview_resistor(snapped_x, snapped_y)
        elif self._placement_type == "C":
            self._draw_preview_capacitor(snapped_x, snapped_y)
        elif self._placement_type == "OPAMP":
            self._draw_preview_opamp(snapped_x, snapped_y)
        elif self._placement_type == "V":
            self._draw_preview_voltage_source(snapped_x, snapped_y)
        elif self._placement_type == "I":
            self._draw_preview_current_source(snapped_x, snapped_y)
        elif self._placement_type == "GND":
            self._draw_preview_ground(snapped_x, snapped_y)
        elif self._placement_type == "VOUT":
            self._draw_preview_vout(snapped_x, snapped_y)

    def _draw_preview_resistor(self, x: float, y: float):
        """Draw preview resistor."""
        half_len = 25.0
        pin_offset = 15.0
        pin1_x, pin1_y = self._snap_to_grid(x - half_len - pin_offset, y)
        pin2_x, pin2_y = self._snap_to_grid(x + half_len + pin_offset, y)
        
        center_x = (pin1_x + pin2_x) / 2
        center_y = (pin1_y + pin2_y) / 2
        dx = pin2_x - pin1_x
        dy = pin2_y - pin1_y
        length = (dx**2 + dy**2)**0.5
        
        if length == 0:
            return
        
        cos_theta = dx / length
        sin_theta = dy / length
        
        body_length = 50.0
        body_height = 16.0
        half_len = body_length / 2
        half_height = body_height / 2
        
        # Calculate rectangle corners in local coordinates
        corners_local = [
            (-half_len, -half_height),  # Top-left
            (half_len, -half_height),   # Top-right
            (half_len, half_height),    # Bottom-right
            (-half_len, half_height),   # Bottom-left
        ]
        
        # Transform corners to world coordinates
        corners_world = []
        for x_local, y_local in corners_local:
            x_world = center_x + x_local * cos_theta - y_local * sin_theta
            y_world = center_y + x_local * sin_theta + y_local * cos_theta
            corners_world.append(QPointF(x_world, y_world))
        
        # Draw rectangle using polygon
        scene = self.scene()
        rect_polygon = QPolygonF(corners_world)
        rect_item = QGraphicsPolygonItem(rect_polygon)
        rect_item.setPen(self._preview_component_pen)
        scene.addItem(rect_item)
        self._preview_component_items.append(rect_item)
        
        # Calculate body end points (where wires connect)
        body_end1_x = center_x - half_len * cos_theta
        body_end1_y = center_y - half_len * sin_theta
        body_end2_x = center_x + half_len * cos_theta
        body_end2_y = center_y + half_len * sin_theta
        
        # Draw short wires from body to pins
        wire1 = QGraphicsLineItem(body_end1_x, body_end1_y, pin1_x, pin1_y)
        wire1.setPen(self._preview_component_pen)
        scene.addItem(wire1)
        self._preview_component_items.append(wire1)
        
        wire2 = QGraphicsLineItem(body_end2_x, body_end2_y, pin2_x, pin2_y)
        wire2.setPen(self._preview_component_pen)
        scene.addItem(wire2)
        self._preview_component_items.append(wire2)

    def _draw_preview_capacitor(self, x: float, y: float):
        """Draw preview capacitor."""
        pin_offset = 15.0
        pin1_x, pin1_y = self._snap_to_grid(x - pin_offset, y)
        pin2_x, pin2_y = self._snap_to_grid(x + pin_offset, y)
        
        center_x = (pin1_x + pin2_x) / 2
        center_y = (pin1_y + pin2_y) / 2
        dx = pin2_x - pin1_x
        dy = pin2_y - pin1_y
        length = (dx**2 + dy**2)**0.5
        
        if length == 0:
            return
        
        cos_theta = dx / length
        sin_theta = dy / length
        
        plate_length = 20.0
        plate_spacing = 10.0
        
        left_x = center_x - plate_spacing/2 * cos_theta
        left_y = center_y - plate_spacing/2 * sin_theta
        p1_x = left_x - plate_length/2 * sin_theta
        p1_y = left_y + plate_length/2 * cos_theta
        p2_x = left_x + plate_length/2 * sin_theta
        p2_y = left_y - plate_length/2 * cos_theta
        
        scene = self.scene()
        line1 = QGraphicsLineItem(p1_x, p1_y, p2_x, p2_y)
        line1.setPen(self._preview_component_pen)
        scene.addItem(line1)
        self._preview_component_items.append(line1)
        
        right_x = center_x + plate_spacing/2 * cos_theta
        right_y = center_y + plate_spacing/2 * sin_theta
        p3_x = right_x - plate_length/2 * sin_theta
        p3_y = right_y + plate_length/2 * cos_theta
        p4_x = right_x + plate_length/2 * sin_theta
        p4_y = right_y - plate_length/2 * cos_theta
        
        line2 = QGraphicsLineItem(p3_x, p3_y, p4_x, p4_y)
        line2.setPen(self._preview_component_pen)
        scene.addItem(line2)
        self._preview_component_items.append(line2)
        
        # Draw short wires from plates to pins
        # Left plate center connects to pin1
        wire1 = QGraphicsLineItem(left_x, left_y, pin1_x, pin1_y)
        wire1.setPen(self._preview_component_pen)
        scene.addItem(wire1)
        self._preview_component_items.append(wire1)
        
        # Right plate center connects to pin2
        wire2 = QGraphicsLineItem(right_x, right_y, pin2_x, pin2_y)
        wire2.setPen(self._preview_component_pen)
        scene.addItem(wire2)
        self._preview_component_items.append(wire2)

    def _draw_preview_opamp(self, x: float, y: float):
        """Draw preview op-amp."""
        size = 40.0
        tip_x = x + size
        tip_y = y
        left_x = x - size/2
        top_y = y - size/2
        bottom_y = y + size/2
        
        scene = self.scene()
        triangle = QPolygonF([
            QPointF(tip_x, tip_y),
            QPointF(left_x, top_y),
            QPointF(left_x, bottom_y),
        ])
        triangle_item = QGraphicsPolygonItem(triangle)
        triangle_item.setPen(self._preview_component_pen)
        scene.addItem(triangle_item)
        self._preview_component_items.append(triangle_item)

    def _draw_preview_voltage_source(self, x: float, y: float):
        """Draw preview voltage source."""
        radius = 15.0
        wire_length = 10.0
        
        scene = self.scene()
        circle = QGraphicsEllipseItem(x - radius, y - radius, radius * 2, radius * 2)
        circle.setPen(self._preview_component_pen)
        scene.addItem(circle)
        self._preview_component_items.append(circle)
        
        # Draw short wires
        wire1 = QGraphicsLineItem(x, y - wire_length, x, y - radius)
        wire1.setPen(self._preview_component_pen)
        scene.addItem(wire1)
        self._preview_component_items.append(wire1)
        
        wire2 = QGraphicsLineItem(x, y + radius, x, y + wire_length)
        wire2.setPen(self._preview_component_pen)
        scene.addItem(wire2)
        self._preview_component_items.append(wire2)

    def _draw_preview_current_source(self, x: float, y: float):
        """Draw preview current source."""
        radius = 15.0
        wire_length = 10.0
        
        scene = self.scene()
        circle = QGraphicsEllipseItem(x - radius, y - radius, radius * 2, radius * 2)
        circle.setPen(self._preview_component_pen)
        scene.addItem(circle)
        self._preview_component_items.append(circle)
        
        # Draw short wires
        wire1 = QGraphicsLineItem(x, y - wire_length, x, y - radius)
        wire1.setPen(self._preview_component_pen)
        scene.addItem(wire1)
        self._preview_component_items.append(wire1)
        
        wire2 = QGraphicsLineItem(x, y + radius, x, y + wire_length)
        wire2.setPen(self._preview_component_pen)
        scene.addItem(wire2)
        self._preview_component_items.append(wire2)
        
        # Draw arrow
        arrow_size = 8.0
        arrow_tip_x = x + arrow_size
        arrow_tip_y = y
        arrow_left_x = x - arrow_size/2
        arrow_left_y = y - arrow_size/3
        arrow_right_x = x - arrow_size/2
        arrow_right_y = y + arrow_size/3
        
        arrow_line = QGraphicsLineItem(arrow_left_x, y, arrow_tip_x, arrow_tip_y)
        arrow_line.setPen(self._preview_component_pen)
        scene.addItem(arrow_line)
        self._preview_component_items.append(arrow_line)

    def _draw_preview_ground(self, x: float, y: float):
        """Draw preview ground."""
        line_length = 12.0
        spacing = 4.0
        
        scene = self.scene()
        line1 = QGraphicsLineItem(x - line_length/2, y, x + line_length/2, y)
        line1.setPen(self._preview_component_pen)
        scene.addItem(line1)
        self._preview_component_items.append(line1)
        
        line2 = QGraphicsLineItem(x - line_length/2 + spacing, y + spacing, 
                                 x + line_length/2 - spacing, y + spacing)
        line2.setPen(self._preview_component_pen)
        scene.addItem(line2)
        self._preview_component_items.append(line2)
        
        line3 = QGraphicsLineItem(x - line_length/2 + spacing*2, y + spacing*2, 
                                 x + line_length/2 - spacing*2, y + spacing*2)
        line3.setPen(self._preview_component_pen)
        scene.addItem(line3)
        self._preview_component_items.append(line3)

    def _draw_preview_vout(self, x: float, y: float):
        """Draw preview VOUT."""
        scene = self.scene()
        pin_item = QGraphicsEllipseItem(x - 2, y - 2, 4, 4)
        pin_item.setPen(self._preview_component_pen)
        scene.addItem(pin_item)
        self._preview_component_items.append(pin_item)

    def mousePressEvent(self, event):
        """Handle mouse press events."""
        scene_pt = self.mapToScene(event.pos())
        x, y = scene_pt.x(), scene_pt.y()
        
        if self._mode == "place":
            if event.button() == Qt.MouseButton.LeftButton:
                self._place_component_at(x, y, self._placement_type)
            return
        
        elif self._mode == "wire":
            if event.button() == Qt.MouseButton.LeftButton:
                # Find nearest pin or wire
                nearest_pin = None
                min_dist = float('inf')
                for comp in self.model.components:
                    for pin in comp.pins:
                        dist = ((pin.x - x)**2 + (pin.y - y)**2)**0.5
                        if dist < min_dist and dist < 10.0:  # 10 pixel tolerance
                            min_dist = dist
                            nearest_pin = pin
                
                if nearest_pin:
                    if self._pending_pin is None:
                        # Start wire from pin
                        self._pending_pin = nearest_pin
                        self._preview_wire_start = (nearest_pin.x, nearest_pin.y)
                    else:
                        # Complete wire to pin
                        if self._pending_pin != nearest_pin:
                            wire = SchematicWire(
                                x1=self._pending_pin.x,
                                y1=self._pending_pin.y,
                                x2=nearest_pin.x,
                                y2=nearest_pin.y,
                                net="",  # Will be assigned by net extraction
                            )
                            self.model.wires.append(wire)
                            self._redraw_from_model()
                        self._pending_pin = None
                        self._preview_wire_start = None
                        self._clear_preview_wire()
                else:
                    # No pin clicked - check if we should start wire from empty space or connect to wire
                    if self._preview_wire_start is None:
                        # First click on empty space - start wire from this position
                        snapped_x, snapped_y = self._snap_to_grid(x, y)
                        self._preview_wire_start = (snapped_x, snapped_y)
                    else:
                        # Second click - check for wire-to-wire connection or complete wire
                        nearest_wire = find_nearest_wire(self.model.wires, x, y)
                        if nearest_wire and self._pending_pin:
                            # Connect from pending pin to existing wire via junction
                            junction = SchematicJunction(x=x, y=y)
                            self.model.junctions.append(junction)
                            # Create wire from pending pin to junction
                            wire1 = SchematicWire(
                                x1=self._pending_pin.x,
                                y1=self._pending_pin.y,
                                x2=x,
                                y2=y,
                                net="",  # Will be assigned by net extraction
                            )
                            self.model.wires.append(wire1)
                            # Create wire from junction to nearest wire
                            wire2 = SchematicWire(
                                x1=x,
                                y1=y,
                                x2=nearest_wire.x2,
                                y2=nearest_wire.y2,
                                net="",  # Will be assigned by net extraction
                            )
                            self.model.wires.append(wire2)
                            self._redraw_from_model()
                            self._pending_pin = None
                            self._preview_wire_start = None
                            self._clear_preview_wire()
                        elif self._preview_wire_start:
                            # Complete wire from start position to current position
                            start_x, start_y = self._preview_wire_start
                            snapped_x, snapped_y = self._snap_to_grid(x, y)
                            wire = SchematicWire(
                                x1=start_x,
                                y1=start_y,
                                x2=snapped_x,
                                y2=snapped_y,
                                net="",  # Will be assigned by net extraction
                            )
                            self.model.wires.append(wire)
                            self._redraw_from_model()
                            self._preview_wire_start = None
                            self._clear_preview_wire()
            return
        
        elif self._mode == "delete":
            if event.button() == Qt.MouseButton.LeftButton:
                # Find and delete wire or component
                clicked_wire = None
                for wire_item, wire in self._wire_items.items():
                    # Get the actual line item endpoints (for Manhattan routing segments)
                    line_x1 = wire_item.line().x1()
                    line_y1 = wire_item.line().y1()
                    line_x2 = wire_item.line().x2()
                    line_y2 = wire_item.line().y2()
                    
                    # Check distance to this line segment
                    wire_length = ((line_x2 - line_x1)**2 + (line_y2 - line_y1)**2)**0.5
                    if wire_length > 0:
                        # Check distance to line segment
                        t = max(0, min(1, ((x - line_x1) * (line_x2 - line_x1) + (y - line_y1) * (line_y2 - line_y1)) / (wire_length**2)))
                        proj_x = line_x1 + t * (line_x2 - line_x1)
                        proj_y = line_y1 + t * (line_y2 - line_y1)
                        dist = ((x - proj_x)**2 + (y - proj_y)**2)**0.5
                        if dist < 5.0:
                            clicked_wire = wire
                            break
                
                if clicked_wire:
                    self.model.wires.remove(clicked_wire)
                    self._redraw_from_model()
                    return
                
                # Check for component
                clicked_component = None
                for comp_item, ref in self._component_items.items():
                    if isinstance(comp_item, QGraphicsRectItem):
                        rect = comp_item.rect()
                        if rect.contains(x, y):
                            clicked_component = self._get_component_by_ref(ref)
                            break
                    elif isinstance(comp_item, QGraphicsEllipseItem):
                        rect = comp_item.rect()
                        if rect.contains(x, y):
                            clicked_component = self._get_component_by_ref(ref)
                            break
                
                if clicked_component:
                    self.model.components.remove(clicked_component)
                    self._redraw_from_model()
            return

        elif self._mode == "select":
            if event.button() == Qt.MouseButton.LeftButton:
                # Find clicked component using bounding box (more reliable than graphics item types)
                clicked_component_ref = None
                
                # Check all components to see if click is within their bounding box
                for comp in self.model.components:
                    bbox = self._get_component_bounding_box(comp)
                    if bbox:
                        min_x, min_y, max_x, max_y = bbox
                        if min_x <= x <= max_x and min_y <= y <= max_y:
                            clicked_component_ref = comp.ref
                            break
                
                if clicked_component_ref:
                    # Handle multi-selection with Ctrl/Shift
                    if event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
                        if clicked_component_ref in self._selected_components:
                            self._selected_components.remove(clicked_component_ref)
                        else:
                            self._selected_components.add(clicked_component_ref)
                    else:
                        # Single selection - ensure clicked component is selected
                        if clicked_component_ref not in self._selected_components:
                            self._selected_components.clear()
                        self._selected_components.add(clicked_component_ref)
                    
                    # Store initial positions for dragging
                    self._selected_components_initial_positions.clear()
                    self._selected_components_initial_pins.clear()
                    for ref in self._selected_components:
                        comp = self._get_component_by_ref(ref)
                        if comp:
                            self._selected_components_initial_positions[ref] = (comp.x, comp.y)
                            self._selected_components_initial_pins[ref] = [(p.x, p.y) for p in comp.pins]
                    
                    # Initialize last pin positions for dragging
                    self._pin_last_positions.clear()
                    for ref in self._selected_components:
                        comp = self._get_component_by_ref(ref)
                        if comp:
                            for i, pin in enumerate(comp.pins):
                                self._pin_last_positions[(ref, i)] = (pin.x, pin.y)
                    
                    # Mark that we clicked on a component - this prevents selection rectangle
                    self._clicked_component_ref = clicked_component_ref
                    self._drag_start_pos = scene_pt
                    self._is_dragging = False  # Reset dragging state - will be set when threshold is exceeded
                    # Explicitly clear selection rectangle - we're dragging a component, not selecting
                    self._selection_rect_start = None
                    if self._selection_rect_item:
                        scene = self.scene()
                        try:
                            if self._selection_rect_item.scene() is not None:
                                scene.removeItem(self._selection_rect_item)
                        except RuntimeError:
                            pass
                        self._selection_rect_item = None
                    self._redraw_from_model()
                else:
                    # Clicked empty space - start selection rectangle
                    self._selection_rect_start = scene_pt
                    self._drag_start_pos = scene_pt
                    self._clicked_component_ref = None  # Clear component reference
                    if not (event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)):
                        had_selection = len(self._selected_components) > 0
                        self._selected_components.clear()
                        self._redraw_from_model()
                        # Emit signal if we cleared a selection
                        if had_selection:
                            self.selectionCleared.emit()
            return  # Do NOT call super() - we've fully handled the event
        
        # Fallback: only if some future mode needs default behavior
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle mouse move events."""
        scene_pt = self.mapToScene(event.pos())
        x, y = scene_pt.x(), scene_pt.y()
        
        if self._mode == "place":
            self._update_preview_component(x, y)
            return  # Do NOT call super() - we've fully handled the event
        
        elif self._mode == "wire":
            if self._preview_wire_start:
                scene = self.scene()
                # Clear previous preview wire segments - only clear graphics, don't reset start position
                if self._preview_wire_item:
                    for item in self._preview_wire_item:
                        try:
                            if item.scene() is not None:
                                scene.removeItem(item)
                        except RuntimeError:
                            # Item already deleted, skip it
                            pass
                    self._preview_wire_item.clear()
                
                # Calculate Manhattan route for preview
                start_x, start_y = self._preview_wire_start
                dx = abs(x - start_x)
                dy = abs(y - start_y)
                
                if dx > dy:
                    # Horizontal first, then vertical
                    mid_x = x
                    mid_y = start_y
                else:
                    # Vertical first, then horizontal
                    mid_x = start_x
                    mid_y = y
                
                # Draw Manhattan route with two segments
                # Draw first segment
                line1 = QGraphicsLineItem(start_x, start_y, mid_x, mid_y)
                line1.setPen(self._preview_pen)
                scene.addItem(line1)
                self._preview_wire_item.append(line1)
                
                # Draw second segment
                line2 = QGraphicsLineItem(mid_x, mid_y, x, y)
                line2.setPen(self._preview_pen)
                scene.addItem(line2)
                self._preview_wire_item.append(line2)
            return  # Do NOT call super() - we've fully handled the event
        
        elif self._mode == "select":
            # Priority: component dragging over selection rectangle
            # If we have selected components and clicked on one, NEVER draw selection rectangle
            if self._clicked_component_ref is not None or (self._selected_components and self._drag_start_pos is not None):
                # We're dragging a component - clear any selection rectangle and prevent it from appearing
                if self._selection_rect_item:
                    scene = self.scene()
                    try:
                        if self._selection_rect_item.scene() is not None:
                            scene.removeItem(self._selection_rect_item)
                    except RuntimeError:
                        pass
                    self._selection_rect_item = None
                self._selection_rect_start = None
                
                # Handle dragging if we clicked on a component
                if self._clicked_component_ref is not None and self._drag_start_pos is not None and self._selected_components:
                    dx = x - self._drag_start_pos.x()
                    dy = y - self._drag_start_pos.y()
                    
                    # Check if we've moved enough to start dragging (threshold check only for initial start)
                    # Once dragging starts, components should follow smoothly regardless of movement amount
                    if self._is_dragging or abs(dx) > self._drag_threshold or abs(dy) > self._drag_threshold:
                        if not self._is_dragging:
                            self._is_dragging = True  # Mark that dragging has started
                        
                        # Move all selected components
                        for ref in list(self._selected_components):
                            comp = self._get_component_by_ref(ref)
                            if comp and ref in self._selected_components_initial_positions:
                                orig_x, orig_y = self._selected_components_initial_positions[ref]
                                new_x, new_y = self._snap_to_grid(orig_x + dx, orig_y + dy)
                                
                                # Update component position
                                comp.x = new_x
                                comp.y = new_y
                                
                                # Update pin positions
                                if ref in self._selected_components_initial_pins:
                                    orig_pins = self._selected_components_initial_pins[ref]
                                    for i, pin in enumerate(comp.pins):
                                        if i < len(orig_pins):
                                            orig_pin_x, orig_pin_y = orig_pins[i]
                                            new_pin_x, new_pin_y = self._snap_to_grid(orig_pin_x + dx, orig_pin_y + dy)
                                            
                                            # Get the previous position of this pin during the drag
                                            pin_key = (ref, i)
                                            old_pin_x, old_pin_y = self._pin_last_positions.get(pin_key, (pin.x, pin.y))
                                            
                                            # Update pin position to the new location
                                            pin.x, pin.y = new_pin_x, new_pin_y
                                            
                                            # Move any wires attached to the previous pin position to the new one
                                            self._update_wires_connected_to_pin(old_pin_x, old_pin_y, new_pin_x, new_pin_y)
                                            
                                            # Store the new position as the last-known position
                                            self._pin_last_positions[pin_key] = (new_pin_x, new_pin_y)
                                
                                # Check for collisions
                                if self._check_component_overlap(comp):
                                    # Revert if collision - restore component, pins, and wires
                                    comp.x, comp.y = orig_x, orig_y
                                    if ref in self._selected_components_initial_pins:
                                        orig_pins = self._selected_components_initial_pins[ref]
                                        for i, pin in enumerate(comp.pins):
                                            if i < len(orig_pins):
                                                orig_pin_x, orig_pin_y = orig_pins[i]
                                                # Get the last-known position before reverting
                                                pin_key = (ref, i)
                                                old_pin_x, old_pin_y = self._pin_last_positions.get(pin_key, (pin.x, pin.y))
                                                # Revert wires connected to this pin (from last position back to original)
                                                self._update_wires_connected_to_pin(old_pin_x, old_pin_y, orig_pin_x, orig_pin_y)
                                                # Revert pin position
                                                pin.x, pin.y = orig_pin_x, orig_pin_y
                                                # Update last-known position to original position after revert
                                                self._pin_last_positions[pin_key] = (orig_pin_x, orig_pin_y)
                        
                        self._redraw_from_model()
                        # Do NOT update _drag_start_pos - it should remain fixed at initial click position
                        # This ensures delta is always calculated from the original drag start
            
            elif self._selection_rect_start is not None and self._drag_start_pos is not None and self._clicked_component_ref is None and not self._selected_components:
                # Draw selection rectangle (only if not dragging a component and clicked on empty space)
                scene = self.scene()
                if self._selection_rect_item:
                    try:
                        # Check if item still exists before removing
                        if self._selection_rect_item.scene() is not None:
                            scene.removeItem(self._selection_rect_item)
                    except RuntimeError:
                        # Item already deleted, skip it
                        pass
                
                rect = QRectF(self._selection_rect_start, scene_pt).normalized()
                self._selection_rect_item = QGraphicsRectItem(rect)
                self._selection_rect_item.setPen(self._selection_rect_pen)
                self._selection_rect_item.setBrush(self._selection_rect_brush)
                self._selection_rect_item.setZValue(1000)
                scene.addItem(self._selection_rect_item)
            return  # Do NOT call super() - we've fully handled the event
        
        # Fallback: only if some future mode needs default behavior
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Handle mouse release events."""
        # Check if we were doing a selection rectangle (before it gets cleared)
        was_selection_rect = (self._mode == "select" and 
                             self._selection_rect_start is not None and 
                             self._selection_rect_item is not None)
        
        if was_selection_rect:
            # Finalize selection rectangle
            scene_pt = self.mapToScene(event.pos())
            rect = QRectF(self._selection_rect_start, scene_pt).normalized()
            
            # Find components within rectangle
            for comp in self.model.components:
                bbox = self._get_component_bounding_box(comp)
                if bbox:
                    min_x, min_y, max_x, max_y = bbox
                    comp_rect = QRectF(min_x, min_y, max_x - min_x, max_y - min_y)
                    if rect.intersects(comp_rect):
                        self._selected_components.add(comp.ref)
            
            # Clear selection rectangle
            scene = self.scene()
            if self._selection_rect_item:
                scene.removeItem(self._selection_rect_item)
                self._selection_rect_item = None
            self._selection_rect_start = None
            self._redraw_from_model()
        
        if self._mode == "select":
            # Emit componentClicked signal if a component was clicked (not dragged, not selection rectangle)
            if (self._clicked_component_ref is not None and 
                not self._is_dragging and 
                not was_selection_rect):
                # This was a click on a component, emit the signal
                self.componentClicked.emit(self._clicked_component_ref)
            
            self._drag_start_pos = None
            self._clicked_component_ref = None  # Clear component reference on release
            self._is_dragging = False  # Reset dragging state
            self._pin_last_positions.clear()  # Clear last pin positions for next drag
            return  # Do NOT call super() - we've fully handled the event
        
        # Fallback: only if some future mode needs default behavior
        self._drag_start_pos = None
        self._clicked_component_ref = None
        super().mouseReleaseEvent(event)
