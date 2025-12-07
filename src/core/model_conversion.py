from __future__ import annotations

"""
Model conversion / simplification layer.

Goal:
- Take a ModelMetadata for a vendor op-amp SPICE model (often PSpice / LTspice).
- Optionally auto-generate a simplified, SPICE3-compatible macromodel
  that can be simulated by ngspice and Xyce.

This first version focuses on op-amps and uses a single-pole macromodel:

    .SUBCKT <name> VPLUS VMINUS VOUT VCC VEE
    EOPAMP_INT NINT 0 VPLUS VMINUS A0
    RBUF NINT VOUT 1
    RPOLE VOUT 0 1k
    CPOLE VOUT 0 Cpole
    .ENDS <name>

where:
    A0   = open-loop DC gain
    GBW  = gain-bandwidth product
    fp   = GBW / A0
    Cpole = 1 / (2*pi*R*fp), with R = 1k

This is intentionally approximate and suitable mainly for:
- small-signal AC gain
- bandwidth estimation
- rough noise analysis

It is NOT intended for detailed transient / THD accuracy.
"""

import math
import os
from pathlib import Path
from typing import Optional, Union

from .model_metadata import ModelMetadata, ModelFeatureFlags  # type: ignore[unused-import]


PathLike = Union[str, Path]

# Approximate default op-amp parameters by part name.
# Keys should match the .SUBCKT name (case-insensitive compare).
PART_DEFAULTS = {
    "OP284": {  # ADI OP284 dual op-amp, GBW ~ 4 MHz
        "a0": 2e5,       # 100 dB
        "gbw_hz": 4e6,   # 4 MHz
    },
    "TL072": {  # TI TL072, GBW ~ 3 MHz
        "a0": 2e5,
        "gbw_hz": 3e6,
    },
    # Add more known parts here later...
}



def create_simple_opamp_model(
    meta: ModelMetadata,
    output_dir: Optional[PathLike] = None,
    a0: float = 2e5,
    gbw_hz: float = 4e6,
) -> ModelMetadata:
    """
    Create a simplified, SPICE3-compatible single-pole op-amp macromodel
    based on high-level metadata of a vendor model.

    Args:
        meta:
            Original ModelMetadata returned by analyze_model(path).
        output_dir:
            Directory where the new .lib will be written.
            If None, it is created next to the original file.
        a0:
            Open-loop DC gain (e.g. 2e5 ≈ 106 dB).
        gbw_hz:
            Gain-bandwidth product in Hz (e.g. 4e6 for ~4 MHz).

    Returns:
        A new ModelMetadata instance pointing to the simplified .lib file.
    """
    original_path = Path(meta.path)
    if output_dir is None:
        out_dir = original_path.parent
    else:
        out_dir = Path(output_dir)

    out_dir.mkdir(parents=True, exist_ok=True)

    # Decide on subcircuit name:
    # - Prefer first .SUBCKT name if present
    # - Fallback: basename without extension
    if meta.model_names:
        subckt_name = meta.model_names[0]
    else:
        subckt_name = original_path.stem or "GENERIC_OPAMP"

    # Override a0 / gbw_hz if we have part-specific defaults
    preset = PART_DEFAULTS.get(subckt_name.upper())
    if preset is not None:
        a0 = float(preset.get("a0", a0))
        gbw_hz = float(preset.get("gbw_hz", gbw_hz))    

    # Output filename: <subckt_name>_simple.lib
    out_filename = f"{subckt_name}_simple.lib"
    out_path = out_dir / out_filename

    # Compute pole frequency and C value
    # fp = GBW / A0
    fp = gbw_hz / a0 if a0 != 0 else gbw_hz
    # Choose R = 1k => C = 1 / (2*pi*R*fp)
    r_pole = 1000.0
    if fp <= 0:
        # Extremely defensive: avoid division by zero / negative freq.
        c_pole = 1e-9  # arbitrary small cap
    else:
        c_pole = 1.0 / (2.0 * math.pi * r_pole * fp)

    vendor_str = meta.vendor or "unknown"
    original_file_str = meta.basename or os.path.basename(meta.path)

    # Generate plain SPICE3 text for the simplified model.
    # No PSpice/LTspice extensions, no A-devices, no .control, etc.
    lines = [
        f"* Auto-generated simplified macromodel for {subckt_name}",
        f"* Original file: {original_file_str}",
        f"* Original vendor: {vendor_str}",
        "*",
        "* This model is intentionally approximate.",
        "* It replaces a complex vendor PSpice/LTspice model with",
        "* a single-pole op-amp macromodel suitable for:",
        "*   - small-signal AC gain",
        "*   - bandwidth estimation",
        "*   - rough noise analysis",
        "* It is NOT intended for detailed transient/THD accuracy.",
        "*",
        f".SUBCKT {subckt_name} VPLUS VMINUS VOUT VCC VEE",
        "* Power rails are currently not used in the small-signal model.",
        "* Differential input to internal high-gain node",
        f"EOPAMP_INT NINT 0 VPLUS VMINUS {a0:g}",
        "* Buffer and dominant pole at the output",
        "RBUF NINT VOUT 1",
        f"RPOLE VOUT 0 {r_pole:g}",
        f"CPOLE VOUT 0 {c_pole:g}",
        f".ENDS {subckt_name}",
        "",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")

    # Build new ModelMetadata for the simplified model.
    simplified_meta = ModelMetadata(
        path=str(out_path),
        basename=out_path.name,
        vendor=meta.vendor or "auto-generated",
        model_names=[subckt_name],
        is_standard_spice=True,
        is_pspice=False,
        is_ltspice=False,
        is_encrypted=False,
        supports_ngspice=True,
        supports_xyce=True,
        recommended_simulator="ngspice",
        features=ModelFeatureFlags(),  # empty, plain SPICE
        required_features=set(),
        conversion_needed=False,
        conversion_warnings=[
            (
                "Simplified single-pole op-amp macromodel auto-generated from "
                f"{original_file_str}. "
                "This is an approximate SPICE3-only replacement for the "
                "original vendor model and is not intended for detailed "
                "transient/THD accuracy."
            )
        ],
    )

    return simplified_meta


def maybe_convert_to_simple_opamp(
    meta: ModelMetadata,
    auto_for_nonstandard: bool = True,
    output_dir: Optional[PathLike] = None,
) -> ModelMetadata:
    """
    Decide whether to auto-generate a simplified op-amp macromodel for the
    given vendor model, and if so, return metadata for the new file.

    Behavior v0:

    - If meta.is_standard_spice == True:
        -> Return meta unchanged (no conversion).

    - If meta.is_encrypted == True:
        -> Return meta unchanged (we can't inspect or replace it safely).

    - If auto_for_nonstandard == False:
        -> Return meta unchanged.

    - Otherwise (non-standard PSpice/LTspice-like model):
        -> Auto-generate a simple single-pole op-amp macromodel
           in a new .lib file and return a new ModelMetadata
           pointing to that file.

    Typical usage:

        meta_orig = analyze_model(".../OP284.lib")
        meta_conv = maybe_convert_to_simple_opamp(meta_orig)
        # meta_conv now points to OP284_simple.lib with a single-pole model
    """
    # Case 1: Already standard SPICE -> nothing to do
    if meta.is_standard_spice:
        return meta

    # Case 2: Encrypted/protected -> we cannot safely replace it automatically
    if meta.is_encrypted:
        return meta

    # Case 3: User disabled auto-conversion
    if not auto_for_nonstandard:
        return meta

    # Case 4: Non-standard model (PSpice / LTspice / other)
    # We don't need to distinguish here; the whole point is to create a
    # SPICE3-only replacement.
    return create_simple_opamp_model(
        meta=meta,
        output_dir=output_dir,
        # These defaults can be tuned per-device or exposed in the UI later.
        a0=2e5,
        gbw_hz=4e6,
    )
