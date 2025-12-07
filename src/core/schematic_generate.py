# src/core/schematic_generate.py

from __future__ import annotations
from typing import Optional

from .circuit import Circuit
from .schematic_model import (
    SchematicModel,
    SchematicComponent,
    SchematicWire,
    SchematicPin,
)


def non_inverting_circuit_to_schematic(circuit: Circuit) -> SchematicModel:
    """
    Build a SchematicModel for the standard non-inverting op-amp topology.

    Coordinates are chosen to roughly match the current SchematicView layout.
    """
    model = SchematicModel()

    # --- Basic node positions (just for neat routing) -----------------------
    vin_x, vin_y = -160.0, 0.0
    plus_x, plus_y = 0.0, 0.0
    minus_x, minus_y = 0.0, 40.0
    out_x, out_y = 160.0, 20.0

    # Helper to get component value from Circuit (by ref)
    def get_value(ref: str, default: float = 0.0) -> float:
        comp = circuit.get_component(ref)
        return float(comp.value) if comp is not None else default

    # --- Rin: between Vin and Vplus ----------------------------------------
    rin_center_x, rin_center_y = -80.0, plus_y
    rin_value = get_value("Rin", 10_000.0)
    rin_pins = [
        SchematicPin(name="1", x=vin_x, y=vin_y, net="Vin"),
        SchematicPin(name="2", x=plus_x, y=plus_y, net="Vplus"),
    ]
    model.components.append(
        SchematicComponent(
            ref="Rin",
            ctype="R",
            value=rin_value,
            pins=rin_pins,
            x=rin_center_x,
            y=rin_center_y,
            rotation=0.0,  # horizontal
        )
    )
    # Wire segments for Rin (just two straight pieces)
    model.wires.append(SchematicWire(x1=vin_x, y1=vin_y, x2=-105.0, y2=plus_y, net="Vin"))
    model.wires.append(SchematicWire(x1=-55.0, y1=plus_y, x2=plus_x, y2=plus_y, net="Vplus"))

    # --- R1: feedback from Vout to Vminus -----------------------------------
    r1_y = -40.0
    r1_center_x = 80.0
    r1_value = get_value("R1", 90_000.0)
    r1_pins = [
        SchematicPin(name="1", x=out_x, y=r1_y, net="Vout"),
        SchematicPin(name="2", x=minus_x, y=r1_y, net="Vminus"),
    ]
    model.components.append(
        SchematicComponent(
            ref="R1",
            ctype="R",
            value=r1_value,
            pins=r1_pins,
            x=r1_center_x,
            y=r1_y,
            rotation=0.0,  # horizontal
        )
    )
    # Wire from minus node up to R1, then over to out_x, then down to output node
    model.wires.append(SchematicWire(x1=minus_x, y1=minus_y, x2=minus_x, y2=r1_y, net="Vminus"))
    model.wires.append(SchematicWire(x1=minus_x, y1=r1_y, x2=40.0, y2=r1_y, net="Vminus"))
    model.wires.append(SchematicWire(x1=120.0, y1=r1_y, x2=out_x, y2=r1_y, net="Vout"))
    model.wires.append(SchematicWire(x1=out_x, y1=r1_y, x2=out_x, y2=out_y, net="Vout"))

    # --- R2: from Vminus to ground -----------------------------------------
    r2_center_y = 110.0
    r2_value = get_value("R2", 10_000.0)
    r2_pins = [
        SchematicPin(name="1", x=minus_x, y=minus_y, net="Vminus"),
        SchematicPin(name="2", x=minus_x, y=r2_center_y + 40.0, net="0"),  # ground below
    ]
    model.components.append(
        SchematicComponent(
            ref="R2",
            ctype="R",
            value=r2_value,
            pins=r2_pins,
            x=minus_x,
            y=r2_center_y,
            rotation=90.0,  # vertical
        )
    )
    # Wire from minus down to top of R2, and from R2 bottom to ground node
    model.wires.append(SchematicWire(x1=minus_x, y1=minus_y, x2=minus_x, y2=85.0, net="Vminus"))
    model.wires.append(SchematicWire(x1=minus_x, y1=135.0, x2=minus_x, y2=160.0, net="0"))

    # --- Op-amp block -------------------------------------------------------
    # We'll keep OPAMP fairly abstract for now; pins at the known locations.
    opamp_pins = [
        SchematicPin(name="+", x=plus_x, y=plus_y, net="Vplus"),
        SchematicPin(name="-", x=minus_x, y=minus_y, net="Vminus"),
        SchematicPin(name="OUT", x=out_x, y=out_y, net="Vout"),
    ]
    model.components.append(
        SchematicComponent(
            ref="U1",
            ctype="OPAMP",
            value=0.0,
            pins=opamp_pins,
            x=40.0,
            y=20.0,
            rotation=0.0,
        )
    )
    # Wires for op-amp symbol will be drawn directly in the view from these positions.

    return model
