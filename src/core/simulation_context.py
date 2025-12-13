"""
Simulation context logging utilities.

Provides functions to generate simulation context banners that show:
- Which simulator backend was used
- Whether model conversion was used
- Whether ngspice PSpice compatibility mode was enabled
"""

from __future__ import annotations
from typing import Optional

from .model_metadata import ModelMetadata


def generate_simulation_context_banner(
    simulator_name: str,
    conversion_used: bool,
    run_mode: str,
    ngspice_pspice_compat: bool = False,
    meta_original: Optional[ModelMetadata] = None,
    meta_converted: Optional[ModelMetadata] = None,
    fallback_occurred: bool = False,
    initial_backend: Optional[str] = None,
) -> str:
    """
    Generate a simulation context banner for logging.
    
    Args:
        simulator_name: Name of the simulator backend used ("ngspice" or "xyce")
        conversion_used: True if model conversion was used, False if original model was used
        run_mode: "converted_model" or "original_model"
        ngspice_pspice_compat: True if ngspice PSpice compatibility mode was enabled
        meta_original: Original model metadata (for warning detection)
        meta_converted: Converted model metadata (for warning detection)
        fallback_occurred: True if automatic fallback from ngspice to Xyce occurred
        initial_backend: Name of the backend that was tried first (if fallback occurred)
    
    Returns:
        Multi-line string banner to be logged
    """
    lines = []
    lines.append("=" * 60)
    lines.append("Simulation Context")
    lines.append("=" * 60)
    lines.append(f"Simulator: {simulator_name}")
    
    if fallback_occurred and initial_backend:
        lines.append(f"Backend fallback: {initial_backend} -> {simulator_name} (MIF/code-model error)")
    
    if conversion_used:
        lines.append("Model conversion: USED")
    else:
        lines.append("Model conversion: NOT USED")
    
    lines.append(f"Run mode: {run_mode}")
    
    # Only show PSpice compatibility line for ngspice
    if simulator_name == "ngspice":
        if ngspice_pspice_compat:
            lines.append("ngspice PSpice compatibility: ENABLED")
        else:
            lines.append("ngspice PSpice compatibility: DISABLED")
    else:
        lines.append("ngspice PSpice compatibility: N/A")
    
    # Check for warning: model is PSpice-like but user enabled checkbox and still routing to ngspice
    if (simulator_name == "ngspice" and 
        ngspice_pspice_compat and 
        meta_original is not None and
        meta_original.is_pspice and
        meta_original.recommended_simulator == "xyce"):
        lines.append("")
        lines.append("Warning: model is PSpice-like; ngspice run is best-effort (compat enabled).")
    
    lines.append("=" * 60)
    lines.append("")
    
    return "\n".join(lines)
