# src/app/schematic_view.py

from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsLineItem, QGraphicsEllipseItem, QGraphicsTextItem, QGraphicsPolygonItem, QGraphicsRectItem
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtGui import QPen, QPainter, QBrush, QColor, QPolygonF, QTransform, QWheelEvent
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtSvg import QSvgRenderer
from pathlib import Path
import math

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
        self._grid_size = 8.0  # pixels
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
            "L": 0,
            "D": 0,
            "Q": 0,
            "M": 0,
            "M_bulk": 0,
            "G": 0,
            "OPAMP": 0,
            "OPAMP_ideal": 0,
            "V": 0,
            "I": 0,
            "GND": 0,
            "VOUT": 0,
        }
        
        # SVG symbol renderer cache
        self._svg_renderers: dict[str, QSvgRenderer] = {}
        self._load_svg_symbols()
        
        # Set drag mode based on initial mode
        self._update_drag_mode()

    def _load_svg_symbols(self):
        """Load SVG symbols from resources/symbols directory."""
        # Map component types to SVG filenames
        symbol_map = {
            "R": "passive_resistor.svg",
            "C": "passive_capacitor.svg",
            "L": "passive_inductor.svg",
            "D": "diode_standard.svg",
            "Q": "transistor_npn.svg",  # Default to NPN, can be overridden
            "Q_NPN": "transistor_npn.svg",
            "Q_PNP": "transistor_pnp.svg",
            "M": "transistor_nmos.svg",  # 3-terminal MOSFET - Default to NMOS, can be overridden
            "M_NMOS": "transistor_nmos.svg",
            "M_PMOS": "transistor_pmos.svg",
            "M_bulk": "transistor_nmos.svg",  # 4-terminal MOSFET with bulk - Default to NMOS, can be overridden
            "M_bulk_NMOS": "transistor_nmos_bulk.svg",
            "M_bulk_PMOS": "transistor_pmos_bulk.svg",
            "G": "controlled_vccs.svg",
            "OPAMP": "ic_opamp.svg",  # 5-terminal opamp with supply pins
            "OPAMP_ideal": "ic_opamp_ideal.svg",  # 3-terminal ideal opamp
            "V": "source_dc.svg",
            "I": "source_current.svg",
            "GND": "passive_ground.svg",
            "VOUT": "output_node.svg",
        }
        
        base_dir = Path(__file__).parent.parent
        symbols_dir = base_dir / "resources" / "symbols"
        
        for ctype, filename in symbol_map.items():
            svg_path = symbols_dir / filename
            if svg_path.exists():
                renderer = QSvgRenderer(str(svg_path))
                if renderer.isValid():
                    self._svg_renderers[ctype] = renderer
            else:
                print(f"Warning: SVG symbol not found: {svg_path}")

        # Auto net name generator for wires created by the user
        self._next_auto_net_id: int = 1

        # Hover state
        self._hovered_component_item = None
        self._hovered_pin = None

        # Preview wire (temporary wire while dragging) - list for Manhattan routing segments
        self._preview_wire_item = []
        self._preview_wire_start = None
        
        # Preview component (temporary component while placing)
        self._preview_component_items = []  # List of items to clear
        
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
                # Calculate component center from pins
                if not comp.pins:
                    return False
                
                pin_x_coords = [p.x for p in comp.pins]
                pin_y_coords = [p.y for p in comp.pins]
                center_x = (min(pin_x_coords) + max(pin_x_coords)) / 2
                center_y = (min(pin_y_coords) + max(pin_y_coords)) / 2
                
                # Store old pin positions before rotation (for wire updates)
                old_pin_positions = [(pin.x, pin.y) for pin in comp.pins]
                
                # Rotate each pin position around the component center by 90 degrees
                new_pin_positions = []
                for pin in comp.pins:
                    rotated_x, rotated_y = self._rotate_pin_position(
                        pin.x, pin.y, center_x, center_y, 90.0
                    )
                    pin.x = rotated_x
                    pin.y = rotated_y
                    new_pin_positions.append((rotated_x, rotated_y))
                
                # Update the rotation angle
                comp.rotation = (comp.rotation + 90) % 360
                
                # Update wires connected to this component's pins
                # Wires store absolute coordinates, so they need to be updated too
                self._update_wires_for_rotated_component(old_pin_positions, new_pin_positions)
                
                self._redraw_from_model()
                return True
        return False
    
    def _update_wires_for_rotated_component(self, old_pin_positions: list, new_pin_positions: list):
        """Update wire endpoints that are connected to this component's pins after rotation."""
        if not self.model or not self.model.wires:
            return
        
        # Create a mapping from old to new positions
        pin_position_map = dict(zip(old_pin_positions, new_pin_positions))
        
        # Update wire endpoints that match any of the old pin positions
        tolerance = 0.1  # Small tolerance for floating point comparison
        for wire in self.model.wires:
            for old_pos, new_pos in pin_position_map.items():
                old_x, old_y = old_pos
                new_x, new_y = new_pos
                # Check if wire start point matches this old pin position
                if abs(wire.x1 - old_x) < tolerance and abs(wire.y1 - old_y) < tolerance:
                    wire.x1, wire.y1 = new_x, new_y
                # Check if wire end point matches this old pin position
                if abs(wire.x2 - old_x) < tolerance and abs(wire.y2 - old_y) < tolerance:
                    wire.x2, wire.y2 = new_x, new_y

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
                    self._draw_selection_rectangle(comp, scene)

    def _draw_component(self, comp: SchematicComponent):
        """Draw a single component using SVG symbols."""
        # Use SVG-based rendering for all components
        self._draw_component_svg(comp)
    
    def _draw_selection_rectangle(self, comp: SchematicComponent, scene: QGraphicsScene):
        """Draw a rotated selection rectangle around a component."""
        if not comp.pins:
            return
        
        # Get component center from pins
        pin_x_coords = [p.x for p in comp.pins]
        pin_y_coords = [p.y for p in comp.pins]
        center_x = (min(pin_x_coords) + max(pin_x_coords)) / 2
        center_y = (min(pin_y_coords) + max(pin_y_coords)) / 2
        
        # For MOSFET components, adjust the horizontal center calculation (same as symbol rendering)
        # 4-terminal MOSFET: pins span symmetrically (gate at x-32, bulk at x+32), so center is at x
        # 3-terminal MOSFET: pins span from x-32 to x (no bulk), so pin-based center would be x-16
        # To align both types, adjust 3-terminal center by adding half the missing span (16 pixels)
        if comp.ctype == "M":
            # For 3-terminal MOSFET, adjust center to account for missing bulk pin
            center_x = center_x + 16.0
        
        # Get SVG renderer to determine aspect ratio (same logic as _draw_component_svg)
        svg_key = comp.ctype
        if comp.ctype == "Q":
            polarity = comp.extra.get("polarity", "NPN")
            svg_key = f"Q_{polarity}" if polarity in ("NPN", "PNP") else "Q"
        elif comp.ctype == "M":
            mos_type = comp.extra.get("mos_type", "NMOS")
            svg_key = f"M_{mos_type}" if mos_type in ("NMOS", "PMOS") else "M"
        elif comp.ctype == "M_bulk":
            mos_type = comp.extra.get("mos_type", "NMOS")
            svg_key = f"M_bulk_{mos_type}" if mos_type in ("NMOS", "PMOS") else "M_bulk"
        
        renderer = self._svg_renderers.get(svg_key) or self._svg_renderers.get(comp.ctype)
        if renderer:
            viewbox = renderer.viewBoxF()
            if viewbox.isValid():
                svg_aspect = viewbox.height() / viewbox.width() if viewbox.width() > 0 else 1.0
            else:
                svg_aspect = 1.0
        else:
            svg_aspect = 1.0
        
        # Calculate symbol dimensions using the same logic as _draw_component_svg
        # The key is to get the pin distance (which is rotation-invariant) and use that
        # to determine the base symbol dimensions
        if len(comp.pins) >= 2:
            # Get pin distance (this is the same regardless of rotation)
            pin1 = comp.pins[0]
            pin2 = comp.pins[1]
            dx = pin2.x - pin1.x
            dy = pin2.y - pin1.y
            pin_distance = (dx**2 + dy**2)**0.5
            
            # Calculate bounding box of pins (for determining orientation)
            pin_min_x = min(p.x for p in comp.pins)
            pin_max_x = max(p.x for p in comp.pins)
            pin_min_y = min(p.y for p in comp.pins)
            pin_max_y = max(p.y for p in comp.pins)
            
            pin_width = pin_max_x - pin_min_x
            pin_height = pin_max_y - pin_min_y
            
            if comp.ctype in ("R", "C", "L", "D"):
                # Horizontal components: base width is pin distance, height from aspect
                # These components are wider than tall in unrotated state
                base_width = pin_distance
                base_height = base_width * svg_aspect
            elif comp.ctype in ("V", "I"):
                # Vertical components: base height is pin distance, width from aspect
                # These components are taller than wide in unrotated state
                base_height = pin_distance
                base_width = base_height / svg_aspect if svg_aspect > 0 else base_height
            elif comp.ctype in ("Q", "M", "M_bulk", "G"):
                # Square components: use max span
                max_span = max(pin_width, pin_height)
                base_width = max_span
                base_height = max_span
            elif comp.ctype == "OPAMP" or comp.ctype == "OPAMP_ideal":
                # Op-amp: use max of pin_width and pin_height to handle rotation
                # This ensures the selection box doesn't shrink when rotated
                base_width = max(pin_width, pin_height)
                base_height = base_width * svg_aspect
            else:
                # Default: use pin distance
                base_width = max(pin_distance, 64.0)
                base_height = base_width * svg_aspect
        else:
            # Single pin or default
            base_width = 50.0
            base_height = base_width * svg_aspect
        
        # The selection box should always use the base (unrotated) dimensions
        # regardless of rotation - rotation only changes orientation, not size
        symbol_width = base_width
        symbol_height = base_height
        
        # Add padding
        padding = 3.0
        rect_width = base_width + 2 * padding
        rect_height = base_height + 2 * padding
        
        # Create rectangle centered at origin (0, 0) in local coordinates
        rect_item = QGraphicsRectItem(-rect_width/2, -rect_height/2, rect_width, rect_height)
        rect_item.setPen(self._selection_pen)
        rect_item.setZValue(1000)  # Above everything
        
        # Set the transform origin to the center of the rectangle (0, 0 in local coordinates)
        rect_item.setTransformOriginPoint(0, 0)
        
        # Position rectangle at component center
        # For BJT components, apply the same offset as the symbol (16 pixels right)
        if comp.ctype == "Q":
            rect_item.setPos(center_x + 16, center_y)
        else:
            rect_item.setPos(center_x, center_y)
        
        # Apply rotation transform if component is rotated
        if comp.rotation != 0:
            rect_item.setRotation(comp.rotation)
        
        scene.addItem(rect_item)
    
    def _rotate_pin_position(self, pin_x: float, pin_y: float, center_x: float, center_y: float, rotation_deg: float) -> tuple[float, float]:
        """
        Rotate a pin position around a center point.
        
        Args:
            pin_x, pin_y: Pin position
            center_x, center_y: Rotation center
            rotation_deg: Rotation angle in degrees
            
        Returns:
            (rotated_x, rotated_y): Rotated pin position
        """
        if rotation_deg == 0:
            return (pin_x, pin_y)
        
        rotation_rad = math.radians(rotation_deg)
        cos_theta = math.cos(rotation_rad)
        sin_theta = math.sin(rotation_rad)
        
        # Calculate relative position
        pin_rel_x = pin_x - center_x
        pin_rel_y = pin_y - center_y
        
        # Apply rotation
        rotated_x = center_x + pin_rel_x * cos_theta - pin_rel_y * sin_theta
        rotated_y = center_y + pin_rel_x * sin_theta + pin_rel_y * cos_theta
        
        return (rotated_x, rotated_y)
    
    def _draw_component_svg(self, comp: SchematicComponent):
        """
        Draw a component using its SVG symbol.
        Handles rotation, pin positions, and symbol scaling.
        """
        scene = self.scene()
        
        # Determine SVG key based on component type and properties
        svg_key = comp.ctype
        if comp.ctype == "Q":
            # BJT: use polarity-specific symbol
            polarity = comp.extra.get("polarity", "NPN")
            svg_key = f"Q_{polarity}" if polarity in ("NPN", "PNP") else "Q"
        elif comp.ctype == "M":
            # 3-terminal MOSFET: use type-specific symbol
            mos_type = comp.extra.get("mos_type", "NMOS")
            svg_key = f"M_{mos_type}" if mos_type in ("NMOS", "PMOS") else "M"
        elif comp.ctype == "M_bulk":
            # 4-terminal MOSFET with bulk: use type-specific symbol
            mos_type = comp.extra.get("mos_type", "NMOS")
            svg_key = f"M_bulk_{mos_type}" if mos_type in ("NMOS", "PMOS") else "M_bulk"
        
        # Get SVG renderer
        renderer = self._svg_renderers.get(svg_key)
        if renderer is None:
            # Fallback: try base type
            renderer = self._svg_renderers.get(comp.ctype)
        
        if renderer is None:
            # No SVG available, fall back to basic shape
            print(f"Warning: No SVG symbol for {comp.ctype}, using fallback drawing")
            self._draw_component_fallback(comp)
            return
        
        # Calculate component bounds and center from pins
        if not comp.pins:
            return
        
        # For components with pins, calculate bounding box
        pin_x_coords = [p.x for p in comp.pins]
        pin_y_coords = [p.y for p in comp.pins]
        min_x, max_x = min(pin_x_coords), max(pin_x_coords)
        min_y, max_y = min(pin_y_coords), max(pin_y_coords)
        
        # Component center (used for symbol placement)
        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2
        
        # For MOSFET components, adjust the horizontal center calculation
        # 4-terminal MOSFET: pins span symmetrically (gate at x-32, bulk at x+32), so center is at x
        # 3-terminal MOSFET: pins span from x-32 to x (no bulk), so pin-based center would be x-16
        # To align both types, adjust 3-terminal center by adding half the missing span (16 pixels)
        # This ensures 3-terminal and 4-terminal MOSFETs align correctly horizontally
        if comp.ctype == "M":
            # For 3-terminal MOSFET, adjust center to account for missing bulk pin
            # Pins span 32 pixels (gate at x-32 to drain/source at x), center calculated as x-16
            # But to align with 4-terminal version, center should be at x, so add 16 pixels
            center_x = center_x + 16.0
        # M_bulk uses the calculated center_x from pins, which is already correct
        
        # Determine symbol size - base on pin spacing or default
        if len(comp.pins) >= 2:
            # For 2-pin components, use distance between pins as reference
            pin1 = comp.pins[0]
            pin2 = comp.pins[1]
            dx = pin2.x - pin1.x
            dy = pin2.y - pin1.y
            pin_distance = (dx**2 + dy**2)**0.5
            
            # Symbol size: match typical component size (50-60 pixels)
            symbol_size = max(pin_distance * 0.6, 40.0)
        else:
            # For single-pin (GND) or multi-pin (op-amp), use default size
            symbol_size = 50.0
        
        # Get SVG viewBox to maintain aspect ratio
        viewbox = renderer.viewBoxF()
        if viewbox.isValid():
            svg_aspect = viewbox.height() / viewbox.width() if viewbox.width() > 0 else 1.0
        else:
            svg_aspect = 1.0
        
        # Calculate symbol dimensions
        # For proper alignment, we need to calculate symbol size based on actual pin positions
        # and ensure SVG pin coordinates map correctly to canvas pin positions
        if len(comp.pins) >= 2:
            # Calculate bounding box of pins
            pin_min_x = min(p.x for p in comp.pins)
            pin_max_x = max(p.x for p in comp.pins)
            pin_min_y = min(p.y for p in comp.pins)
            pin_max_y = max(p.y for p in comp.pins)
            
            pin_width = pin_max_x - pin_min_x
            pin_height = pin_max_y - pin_min_y
            
            # SVG is 64x64, so we need to scale it to match pin spacing
            # If pins span the full width/height of SVG (0 to 64), use pin span directly
            # For 2-pin horizontal: SVG pins at (0,32) and (64,32), so width should match pin distance
            # For 2-pin vertical: SVG pins at (32,0) and (32,64), so height should match pin distance
            
            if comp.ctype in ("R", "C", "L", "D"):
                # Horizontal 2-pin components: SVG pins at (0,32) and (64,32) - 64 pixels apart
                # Use pin distance (rotation-invariant) to determine symbol width
                # For horizontal components, width is the pin distance, height is from aspect ratio
                pin1 = comp.pins[0]
                pin2 = comp.pins[1]
                dx = pin2.x - pin1.x
                dy = pin2.y - pin1.y
                pin_distance = (dx**2 + dy**2)**0.5
                symbol_width = pin_distance  # Use pin distance, not pin_width (which changes with rotation)
                symbol_height = symbol_width * svg_aspect
            elif comp.ctype in ("V", "I"):
                # Vertical 2-pin components: SVG pins at (32,0) and (32,64) - 64 pixels apart
                # Use pin distance (rotation-invariant) to determine symbol height
                # For vertical components, height is the pin distance, width is from aspect ratio
                pin1 = comp.pins[0]
                pin2 = comp.pins[1]
                dx = pin2.x - pin1.x
                dy = pin2.y - pin1.y
                pin_distance = (dx**2 + dy**2)**0.5
                symbol_height = pin_distance  # Use pin distance, not pin_height (which changes with rotation)
                symbol_width = symbol_height / svg_aspect if svg_aspect > 0 else symbol_height
            elif comp.ctype in ("Q", "M", "M_bulk"):
                # 3-4 pin components: SVG pins at (0,32), (32,0), (32,64), (64,32)
                # Pins span 64 pixels in both directions (from -32 to +32 from center)
                # Symbol should be square, size should match the actual pin span
                max_pin_span = max(pin_width, pin_height)
                symbol_size = max_pin_span  # Use exact pin span
                symbol_width = symbol_size
                symbol_height = symbol_size
            elif comp.ctype == "G":
                # VCCS: 4 pins at edges - same as transistors
                max_pin_span = max(pin_width, pin_height)
                symbol_size = max_pin_span  # Use exact pin span
                symbol_width = symbol_size
                symbol_height = symbol_size
            elif comp.ctype == "OPAMP" or comp.ctype == "OPAMP_ideal":
                # Op-amp: In+ at (0,24), In- at (0,40), Out at (64,32)
                # Pins span 64 pixels horizontally in unrotated state
                # Use pin_width for op-amps since they have 3 pins and the span is meaningful
                # But we need to handle rotation - for op-amps, use the larger dimension
                symbol_width = max(pin_width, pin_height)  # Use max to handle rotation
                symbol_height = symbol_width * svg_aspect
            else:
                # Default: use pin distance
                pin1 = comp.pins[0]
                pin2 = comp.pins[1]
                dx = pin2.x - pin1.x
                dy = pin2.y - pin1.y
                length = (dx**2 + dy**2)**0.5
                symbol_width = max(length, 64.0)
                symbol_height = symbol_width * svg_aspect
        else:
            # For op-amps and other multi-pin, use default size
            symbol_width = symbol_size
            symbol_height = symbol_size * svg_aspect
        
        # Create SVG item
        svg_item = QGraphicsSvgItem()
        svg_item.setSharedRenderer(renderer)
        svg_item.setElementId("")  # Render entire SVG
        
        # Position SVG (same approach as preview for consistency)
        # For BJT components, account for the 16-pixel right shift of pins
        if comp.ctype == "Q":
            # BJT pins are shifted 16 pixels right
            # SVG Base is at (0, 32), Collector at (32, 0), Emitter at (32, 64)
            # Adjust positioning to align SVG pins with shifted canvas pins
            svg_item.setPos(center_x - symbol_width / 2 + 16, center_y - symbol_height / 2)
        else:
            svg_item.setPos(center_x - symbol_width / 2, center_y - symbol_height / 2)
        
        # Apply transform: scale and optionally rotate/flip
        scale_x = symbol_width / 64.0
        scale_y = symbol_height / 64.0
        
        # Build transform matching preview logic
        if comp.rotation != 0 or comp.extra.get("flipped", False):
            # Complex transform: translate to symbol center (in local coordinates), rotate/flip, translate back, scale
            # Symbol center in local coordinates is always (symbol_width/2, symbol_height/2)
            transform = QTransform()
            symbol_center_x_local = symbol_width / 2.0
            symbol_center_y_local = symbol_height / 2.0
            
            # Translate to symbol center (in local coordinates)
            transform.translate(symbol_center_x_local, symbol_center_y_local)
            
            # Apply flip if needed (horizontal flip)
            if comp.extra.get("flipped", False):
                transform.scale(-1, 1)
            
            # Apply rotation around symbol center
            if comp.rotation != 0:
                transform.rotate(comp.rotation)
            
            # Translate back from symbol center
            transform.translate(-symbol_center_x_local, -symbol_center_y_local)
            
            # Scale to desired size
            transform.scale(scale_x, scale_y)
            
            svg_item.setTransform(transform)
        else:
            # Simple scaling only (no rotation/flip) - matches preview
            svg_item.setTransform(QTransform().scale(scale_x, scale_y))
        
        scene.addItem(svg_item)
        
        # Store reference for selection/dragging
        # Use a bounding rect item for hit testing
        bbox_item = QGraphicsRectItem(center_x - symbol_width/2, center_y - symbol_height/2, 
                                      symbol_width, symbol_height)
        bbox_item.setPen(Qt.PenStyle.NoPen)  # Invisible, used for hit testing
        bbox_item.setBrush(Qt.BrushStyle.NoBrush)
        scene.addItem(bbox_item)
        
        self._component_items[bbox_item] = comp.ref
        self._component_graphics_to_model[bbox_item] = comp
        self._component_ref_to_graphics[comp.ref] = bbox_item
        
        # Draw pins (small circles at pin positions)
        # Pin positions in the model are already at their final positions (including rotation)
        # So we draw them directly without applying rotation again
        for pin in comp.pins:
            pin_item = QGraphicsEllipseItem(pin.x - 2, pin.y - 2, 4, 4)
            pin_item.setPen(self._pin_pen)
            pin_item.setBrush(self._pin_brush)
            scene.addItem(pin_item)
            self._pin_marker_items[pin_item] = pin
        
        # Draw component label (skip for preview)
        if comp.ref != "PREVIEW":
            label = QGraphicsTextItem(comp.ref)
            label.setPos(center_x - 10, center_y - 25)
            scene.addItem(label)
    
    def _draw_component_preview_svg(self, comp: SchematicComponent):
        """
        Draw a preview version of a component using SVG (semi-transparent).
        Similar to _draw_component_svg but with preview styling.
        """
        scene = self.scene()
        
        # Determine SVG key based on component type and properties
        svg_key = comp.ctype
        if comp.ctype == "Q":
            polarity = comp.extra.get("polarity", "NPN")
            svg_key = f"Q_{polarity}" if polarity in ("NPN", "PNP") else "Q"
        elif comp.ctype == "M":
            mos_type = comp.extra.get("mos_type", "NMOS")
            svg_key = f"M_{mos_type}" if mos_type in ("NMOS", "PMOS") else "M"
        
        renderer = self._svg_renderers.get(svg_key)
        if renderer is None:
            renderer = self._svg_renderers.get(comp.ctype)
        
        if renderer is None:
            return  # No preview if no SVG available
        
        # Calculate component bounds and center from pins
        if not comp.pins:
            return
        
        pin_x_coords = [p.x for p in comp.pins]
        pin_y_coords = [p.y for p in comp.pins]
        min_x, max_x = min(pin_x_coords), max(pin_x_coords)
        min_y, max_y = min(pin_y_coords), max(pin_y_coords)
        
        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2
        
        # Determine symbol size
        if len(comp.pins) >= 2:
            pin1 = comp.pins[0]
            pin2 = comp.pins[1]
            dx = pin2.x - pin1.x
            dy = pin2.y - pin1.y
            pin_distance = (dx**2 + dy**2)**0.5
            symbol_size = max(pin_distance * 0.6, 40.0)
        else:
            symbol_size = 50.0
        
        # Get SVG viewBox
        viewbox = renderer.viewBoxF()
        svg_aspect = viewbox.height() / viewbox.width() if viewbox.isValid() and viewbox.width() > 0 else 1.0
        
        # Calculate symbol dimensions
        if len(comp.pins) >= 2:
            pin1 = comp.pins[0]
            pin2 = comp.pins[1]
            dx = pin2.x - pin1.x
            dy = pin2.y - pin1.y
            length = (dx**2 + dy**2)**0.5
            symbol_width = max(length * 0.7, 40.0)
            symbol_height = symbol_width * svg_aspect
        else:
            symbol_width = symbol_size
            symbol_height = symbol_size * svg_aspect
        
        # Create SVG item with semi-transparent rendering
        svg_item = QGraphicsSvgItem()
        svg_item.setSharedRenderer(renderer)
        svg_item.setOpacity(0.5)  # Semi-transparent for preview
        svg_item.setElementId("")
        # For BJT components, account for the 16-pixel right shift of pins
        if comp.ctype == "Q":
            svg_item.setPos(center_x - symbol_width / 2 + 16, center_y - symbol_height / 2)
        else:
            svg_item.setPos(center_x - symbol_width / 2, center_y - symbol_height / 2)
        scale_x = symbol_width / 64.0
        scale_y = symbol_height / 64.0
        
        # Apply rotation around symbol center (in local coordinates)
        if comp.rotation != 0:
            symbol_center_x_local = symbol_width / 2.0
            symbol_center_y_local = symbol_height / 2.0
            svg_item.setTransform(QTransform()
                                  .translate(symbol_center_x_local, symbol_center_y_local)
                                  .rotate(comp.rotation)
                                  .translate(-symbol_center_x_local, -symbol_center_y_local)
                                  .scale(scale_x, scale_y))
        else:
            svg_item.setTransform(QTransform().scale(scale_x, scale_y))
        
        scene.addItem(svg_item)
        self._preview_component_items.append(svg_item)
        
        # Draw preview pins (semi-transparent)
        # Rotate pin positions around component center if component is rotated
        for pin in comp.pins:
            rotated_x, rotated_y = self._rotate_pin_position(pin.x, pin.y, center_x, center_y, comp.rotation)
            pin_item = QGraphicsEllipseItem(rotated_x - 2, rotated_y - 2, 4, 4)
            pin_item.setPen(self._preview_pen)
            pin_item.setBrush(QBrush(Qt.GlobalColor.gray))
            pin_item.setOpacity(0.5)
            scene.addItem(pin_item)
            self._preview_component_items.append(pin_item)
    
    def _draw_component_fallback(self, comp: SchematicComponent):
        """Fallback drawing method when SVG is not available."""
        # Use existing drawing methods as fallback
        if comp.ctype == "R":
            self._draw_resistor(comp)
        elif comp.ctype == "C":
            self._draw_capacitor(comp)
        elif comp.ctype == "OPAMP" or comp.ctype == "OPAMP_ideal":
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
        elif comp.ctype == "OPAMP" or comp.ctype == "OPAMP_ideal":
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
        # Handle aliases for component types
        type_normalized = component_type
        if component_type in ("BJT", "Q"):
            type_normalized = "Q"
        elif component_type in ("MOSFET", "M"):
            type_normalized = "M"
        elif component_type in ("MOSFET_bulk", "M_bulk"):
            type_normalized = "M_bulk"
        elif component_type in ("VCCS", "G"):
            type_normalized = "G"
        
        prefix_map = {
            "R": "R", "C": "C", "L": "L", "D": "D",
            "Q": "Q", "M": "M", "M_bulk": "M_bulk", "G": "G",
            "OPAMP": "U", "OPAMP_ideal": "U", "V": "V", "I": "I",
            "GND": "GND", "VOUT": "VOUT",
        }
        prefix = prefix_map.get(type_normalized, "X")
        
        # Ensure counter exists for this type
        if type_normalized not in self._component_ref_counters:
            self._component_ref_counters[type_normalized] = 0
        
        self._component_ref_counters[type_normalized] += 1
        num = self._component_ref_counters[type_normalized]
        return f"{prefix}{num}"

    def _create_resistor(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create a resistor component at position (x, y)."""
        # SVG pins at (0,32) and (64,32) - 64 pixels apart
        # Pin spacing should be 32 pixels from center on each side = 64 total
        pin_offset = 32.0
        pin1_x, pin1_y = self._snap_to_grid(x - pin_offset, y)
        pin2_x, pin2_y = self._snap_to_grid(x + pin_offset, y)
        
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
        # SVG pins at (0,32) and (64,32) - 64 pixels apart
        pin_offset = 32.0
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

    def _create_opamp_ideal(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create an ideal op-amp component (3-terminal) at position (x, y)."""
        # SVG pins: In+ at (0,24), In- at (0,40), Out at (64,32)
        # SVG is 64x64, inputs are 8 pixels apart vertically (24 to 40 = 16 pixels, centered at 32)
        # Component center should align with SVG center (32,32)
        input_y_spacing = 8.0  # Half the spacing between inputs (16/2 = 8)
        input_x = x - 32.0  # Inputs are 32 pixels left of center (at SVG x=0)
        output_x = x + 32.0  # Output is 32 pixels right of center (at SVG x=64)
        
        comp = SchematicComponent(
            ref=ref,
            ctype="OPAMP_ideal",
            x=x,
            y=y,
            rotation=0,
            value=0.0,
            pins=[
                SchematicPin(name="+", x=self._snap_to_grid(input_x, y - input_y_spacing)[0], 
                            y=self._snap_to_grid(input_x, y - input_y_spacing)[1], net=None),  # Non-inverting
                SchematicPin(name="-", x=self._snap_to_grid(input_x, y + input_y_spacing)[0], 
                            y=self._snap_to_grid(input_x, y + input_y_spacing)[1], net=None),  # Inverting
                SchematicPin(name="out", x=self._snap_to_grid(output_x, y)[0], 
                            y=self._snap_to_grid(output_x, y)[1], net=None),  # Output
            ],
        )
        return comp
    
    def _create_opamp(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create an op-amp component with supply terminals (5-terminal) at position (x, y)."""
        # SVG pins: In+ at (0,24), In- at (0,40), Out at (64,32)
        # Add supply pins: VCC (top), VEE (bottom) - positioned above and below the triangle
        input_y_spacing = 8.0  # Half the spacing between inputs (16/2 = 8)
        input_x = x - 32.0  # Inputs are 32 pixels left of center (at SVG x=0)
        output_x = x + 32.0  # Output is 32 pixels right of center (at SVG x=64)
        supply_x = x  # Supply pins at center horizontally
        supply_y_spacing = 32.0  # Distance from center for supply pins
        
        comp = SchematicComponent(
            ref=ref,
            ctype="OPAMP",
            x=x,
            y=y,
            rotation=0,
            value=0.0,
            pins=[
                SchematicPin(name="+", x=self._snap_to_grid(input_x, y - input_y_spacing)[0], 
                            y=self._snap_to_grid(input_x, y - input_y_spacing)[1], net=None),  # Non-inverting
                SchematicPin(name="-", x=self._snap_to_grid(input_x, y + input_y_spacing)[0], 
                            y=self._snap_to_grid(input_x, y + input_y_spacing)[1], net=None),  # Inverting
                SchematicPin(name="out", x=self._snap_to_grid(output_x, y)[0], 
                            y=self._snap_to_grid(output_x, y)[1], net=None),  # Output
                SchematicPin(name="VCC", x=self._snap_to_grid(supply_x, y - supply_y_spacing)[0],
                            y=self._snap_to_grid(supply_x, y - supply_y_spacing)[1], net=None),  # Positive supply
                SchematicPin(name="VEE", x=self._snap_to_grid(supply_x, y + supply_y_spacing)[0],
                            y=self._snap_to_grid(supply_x, y + supply_y_spacing)[1], net=None),  # Negative supply
            ],
        )
        return comp

    def _create_voltage_source(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create a voltage source component at position (x, y)."""
        # SVG pins at (32,0) and (32,64) - 64 pixels apart vertically
        pin_offset = 32.0
        pin1_x, pin1_y = self._snap_to_grid(x, y - pin_offset)
        pin2_x, pin2_y = self._snap_to_grid(x, y + pin_offset)
        
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
        # SVG pins at (32,0) and (32,64) - 64 pixels apart vertically
        pin_offset = 32.0
        pin1_x, pin1_y = self._snap_to_grid(x, y - pin_offset)
        pin2_x, pin2_y = self._snap_to_grid(x, y + pin_offset)
        
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
    
    def _create_inductor(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create an inductor component at position (x, y)."""
        # SVG pins at (0,32) and (64,32) - 64 pixels apart
        pin_offset = 32.0
        pin1_x, pin1_y = self._snap_to_grid(x - pin_offset, y)
        pin2_x, pin2_y = self._snap_to_grid(x + pin_offset, y)
        
        comp = SchematicComponent(
            ref=ref,
            ctype="L",
            x=x,
            y=y,
            rotation=0,
            value=1e-3,  # 1 mH default
            pins=[
                SchematicPin(name="1", x=pin1_x, y=pin1_y, net=None),
                SchematicPin(name="2", x=pin2_x, y=pin2_y, net=None),
            ],
        )
        return comp
    
    def _create_diode(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create a diode component at position (x, y)."""
        # SVG pins at (0,32) and (64,32) - 64 pixels apart
        pin_offset = 32.0
        pin1_x, pin1_y = self._snap_to_grid(x - pin_offset, y)
        pin2_x, pin2_y = self._snap_to_grid(x + pin_offset, y)
        
        comp = SchematicComponent(
            ref=ref,
            ctype="D",
            x=x,
            y=y,
            rotation=0,
            value=0.0,
            pins=[
                SchematicPin(name="A", x=pin1_x, y=pin1_y, net=None),  # Anode
                SchematicPin(name="K", x=pin2_x, y=pin2_y, net=None),  # Cathode
            ],
            extra={},  # Model name will be added via properties dialog
        )
        return comp
    
    def _create_bjt(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create a BJT transistor component at position (x, y)."""
        # SVG pins: Base at (0,32), Collector at (32,0), Emitter at (32,64)
        # Pin spacing: 32 pixels from center
        pin_spacing = 32.0
        # Standard BJT pin layout: C (top), B (left), E (bottom)
        # Shift all terminals 16 pixels right (from original + 32 from previous left shift)
        collector_x, collector_y = self._snap_to_grid(x + 16, y - pin_spacing)
        base_x, base_y = self._snap_to_grid(x - pin_spacing + 16, y)
        emitter_x, emitter_y = self._snap_to_grid(x + 16, y + pin_spacing)
        
        comp = SchematicComponent(
            ref=ref,
            ctype="Q",
            x=x,
            y=y,
            rotation=0,
            value=1.0,  # unused for BJTs
            pins=[
                SchematicPin(name="C", x=collector_x, y=collector_y, net=None),  # Collector
                SchematicPin(name="B", x=base_x, y=base_y, net=None),  # Base
                SchematicPin(name="E", x=emitter_x, y=emitter_y, net=None),  # Emitter
            ],
            extra={
                "polarity": "NPN",  # Default to NPN
            },
        )
        return comp
    
    def _create_mosfet_bulk(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create a 4-terminal MOSFET component (with bulk) at position (x, y)."""
        pin_spacing = 32.0
        # Standard MOSFET pin layout: D (top), G (left), S (bottom), B (right, 4-terminal)
        drain_x, drain_y = self._snap_to_grid(x, y - pin_spacing)
        gate_x, gate_y = self._snap_to_grid(x - pin_spacing, y)
        source_x, source_y = self._snap_to_grid(x, y + pin_spacing)
        bulk_x, bulk_y = self._snap_to_grid(x + pin_spacing, y)
        
        comp = SchematicComponent(
            ref=ref,
            ctype="M_bulk",
            x=x,
            y=y,
            rotation=0,
            value=1.0,  # unused for MOSFETs
            pins=[
                SchematicPin(name="D", x=drain_x, y=drain_y, net=None),  # Drain
                SchematicPin(name="G", x=gate_x, y=gate_y, net=None),  # Gate
                SchematicPin(name="S", x=source_x, y=source_y, net=None),  # Source
                SchematicPin(name="B", x=bulk_x, y=bulk_y, net=None),  # Bulk/Substrate
            ],
            extra={
                "mos_type": "NMOS",  # Default to NMOS
            },
        )
        return comp
    
    def _create_mosfet(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create a 3-terminal MOSFET component (no bulk) at position (x, y)."""
        pin_spacing = 32.0
        # 3-terminal MOSFET pin layout: D (top), G (left), S (bottom) - no bulk
        drain_x, drain_y = self._snap_to_grid(x, y - pin_spacing)
        gate_x, gate_y = self._snap_to_grid(x - pin_spacing, y)
        source_x, source_y = self._snap_to_grid(x, y + pin_spacing)
        
        comp = SchematicComponent(
            ref=ref,
            ctype="M",
            x=x,
            y=y,
            rotation=0,
            value=1.0,  # unused for MOSFETs
            pins=[
                SchematicPin(name="D", x=drain_x, y=drain_y, net=None),  # Drain
                SchematicPin(name="G", x=gate_x, y=gate_y, net=None),  # Gate
                SchematicPin(name="S", x=source_x, y=source_y, net=None),  # Source
            ],
            extra={
                "mos_type": "NMOS",  # Default to NMOS
            },
        )
        return comp
    
    def _create_vccs(self, ref: str, x: float, y: float) -> SchematicComponent:
        """Create a voltage-controlled current source (VCCS) component at position (x, y)."""
        # SVG pins: Output at (32,0) and (32,64), Control at (0,32) and (64,32)
        # Pin spacing: 32 pixels from center
        pin_spacing = 32.0
        # VCCS layout: 4 pins arranged in a square
        # Output current pins (IP, IN) - vertical
        ip_x, ip_y = self._snap_to_grid(x, y - pin_spacing)
        in_x, in_y = self._snap_to_grid(x, y + pin_spacing)
        # Control voltage pins (VP, VN) - horizontal
        vp_x, vp_y = self._snap_to_grid(x + pin_spacing, y)
        vn_x, vn_y = self._snap_to_grid(x - pin_spacing, y)
        
        comp = SchematicComponent(
            ref=ref,
            ctype="G",
            x=x,
            y=y,
            rotation=0,
            value=1e-3,  # 1 mS default transconductance
            pins=[
                SchematicPin(name="IP", x=ip_x, y=ip_y, net=None),  # Output current positive
                SchematicPin(name="IN", x=in_x, y=in_y, net=None),  # Output current negative
                SchematicPin(name="VP", x=vp_x, y=vp_y, net=None),  # Control voltage positive
                SchematicPin(name="VN", x=vn_x, y=vn_y, net=None),  # Control voltage negative
            ],
            extra={},
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
        elif component_type == "L":
            comp = self._create_inductor(ref, x, y)
        elif component_type == "D":
            comp = self._create_diode(ref, x, y)
        elif component_type == "Q" or component_type == "BJT":
            comp = self._create_bjt(ref, x, y)
        elif component_type == "M" or component_type == "MOSFET":
            comp = self._create_mosfet(ref, x, y)
        elif component_type == "M_bulk" or component_type == "MOSFET_bulk":
            comp = self._create_mosfet_bulk(ref, x, y)
        elif component_type == "G" or component_type == "VCCS":
            comp = self._create_vccs(ref, x, y)
        elif component_type == "OPAMP":
            comp = self._create_opamp(ref, x, y)
        elif component_type == "OPAMP_ideal":
            comp = self._create_opamp_ideal(ref, x, y)
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
        
        # Use unified preview drawing that creates temporary component and draws it with SVG
        # Create temporary component for preview
        if self._placement_type == "R":
            preview_comp = self._create_resistor("PREVIEW", snapped_x, snapped_y)
        elif self._placement_type == "C":
            preview_comp = self._create_capacitor("PREVIEW", snapped_x, snapped_y)
        elif self._placement_type == "L":
            preview_comp = self._create_inductor("PREVIEW", snapped_x, snapped_y)
        elif self._placement_type == "D":
            preview_comp = self._create_diode("PREVIEW", snapped_x, snapped_y)
        elif self._placement_type == "Q" or self._placement_type == "BJT":
            preview_comp = self._create_bjt("PREVIEW", snapped_x, snapped_y)
        elif self._placement_type == "M" or self._placement_type == "MOSFET":
            preview_comp = self._create_mosfet("PREVIEW", snapped_x, snapped_y)
        elif self._placement_type == "M_bulk" or self._placement_type == "MOSFET_bulk":
            preview_comp = self._create_mosfet_bulk("PREVIEW", snapped_x, snapped_y)
        elif self._placement_type == "G" or self._placement_type == "VCCS":
            preview_comp = self._create_vccs("PREVIEW", snapped_x, snapped_y)
        elif self._placement_type == "OPAMP":
            preview_comp = self._create_opamp("PREVIEW", snapped_x, snapped_y)
        elif self._placement_type == "OPAMP_ideal":
            preview_comp = self._create_opamp_ideal("PREVIEW", snapped_x, snapped_y)
        elif self._placement_type == "V":
            preview_comp = self._create_voltage_source("PREVIEW", snapped_x, snapped_y)
        elif self._placement_type == "I":
            preview_comp = self._create_current_source("PREVIEW", snapped_x, snapped_y)
        elif self._placement_type == "GND":
            preview_comp = self._create_ground("PREVIEW", snapped_x, snapped_y)
        elif self._placement_type == "VOUT":
            preview_comp = self._create_vout("PREVIEW", snapped_x, snapped_y)
        else:
            return
        
        # Draw preview with SVG (semi-transparent)
        self._draw_component_preview_svg(preview_comp)

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
    
    def wheelEvent(self, event: QWheelEvent):
        """Handle mouse wheel events for zooming."""
        # Zoom factor: 1.15 per step (common zoom increment)
        zoom_factor = 1.15
        
        # Determine zoom direction based on wheel delta
        if event.angleDelta().y() > 0:
            # Zoom in
            scale_factor = zoom_factor
        else:
            # Zoom out
            scale_factor = 1.0 / zoom_factor
        
        # Get current scale
        current_scale = self.transform().m11()  # Get horizontal scale factor
        
        # Calculate new scale
        new_scale = current_scale * scale_factor
        
        # Set zoom limits (min 0.1x, max 10x)
        min_scale = 0.1
        max_scale = 10.0
        
        if new_scale < min_scale:
            scale_factor = min_scale / current_scale
            new_scale = min_scale
        elif new_scale > max_scale:
            scale_factor = max_scale / current_scale
            new_scale = max_scale
        
        # Get mouse position in scene coordinates (before zoom)
        scene_pos = self.mapToScene(event.position().toPoint())
        
        # Apply zoom
        self.scale(scale_factor, scale_factor)
        
        # Calculate new mouse position in scene coordinates (after zoom)
        new_scene_pos = self.mapToScene(event.position().toPoint())
        
        # Adjust view position to keep the point under the mouse in the same place
        delta = new_scene_pos - scene_pos
        self.translate(delta.x(), delta.y())
        
        # Accept the event so it's not propagated
        event.accept()
