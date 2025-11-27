from __future__ import annotations

"""
Data structures for vendor SPICE model metadata and feature flags.

These are used by `model_analyzer.py` to classify models as:
- standard SPICE
- PSpice-like
- LTspice-like
- encrypted / unsupported

and to decide which simulator backend (ngspice / Xyce / none) is recommended.
"""

from dataclasses import dataclass, field
from typing import List, Set, Optional


@dataclass
class ModelFeatureFlags:
    """
    Flags describing what kind of non-standard features are present
    in a SPICE model file.
    """

    # Basic indicators
    has_a_devices: bool = False              # PSpice Axxx macromodel devices
    has_table_models: bool = False           # TABLE {expr} = (...) (...) ...
    has_pspice_behav: bool = False           # LIMIT(), UPLIM(), DNLIM(), etc.
    has_ltspice_behav: bool = False          # ddt(), idt(), LT-specific funcs
    has_encryption: bool = False             # .encrypt/.protect/“encrypted”
    has_digital_primitives: bool = False     # digital or mixed-signal stuff
    has_control_blocks: bool = False         # .control / .endc blocks

    # Raw primitive names / special tokens we detected
    primitives: Set[str] = field(default_factory=set)

    def any_nonstandard(self) -> bool:
        """
        Returns True if anything beyond plain SPICE3 is present.
        """
        return (
            self.has_a_devices
            or self.has_table_models
            or self.has_pspice_behav
            or self.has_ltspice_behav
            or self.has_encryption
            or self.has_digital_primitives
        )


@dataclass
class ModelMetadata:
    """
    High-level classification result for a vendor SPICE file.

    This information is used to:
    - decide which simulator backend is preferred (ngspice / Xyce)
    - inform the user about compatibility / limitations
    - drive potential model-conversion steps
    """

    # File / identity
    path: str
    basename: str
    vendor: Optional[str] = None             # e.g. "TI", "ADI", "LTspice"
    model_names: List[str] = field(default_factory=list)  # from .SUBCKT lines

    # Classification
    is_standard_spice: bool = False
    is_pspice: bool = False
    is_ltspice: bool = False
    is_encrypted: bool = False

    # Backend support
    supports_ngspice: bool = False
    supports_xyce: bool = False

    # Preferred simulator
    # "ngspice", "xyce", or "none" (unsupported)
    recommended_simulator: str = "none"

    # Extra info
    features: ModelFeatureFlags = field(default_factory=ModelFeatureFlags)
    required_features: Set[str] = field(default_factory=set)
    conversion_needed: bool = False
    conversion_warnings: List[str] = field(default_factory=list)

    def short_summary(self) -> str:
        """
        One-line summary suitable for logs / GUI tooltips.
        """
        sim = self.recommended_simulator
        if self.is_encrypted:
            status = "encrypted / unsupported"
        elif self.is_standard_spice and sim == "ngspice":
            status = "standard SPICE (ngspice OK)"
        elif self.is_pspice:
            status = f"PSpice-like (prefers {sim})"
        elif self.is_ltspice:
            status = f"LTspice-like (prefers {sim})"
        else:
            status = f"classified, recommended simulator: {sim}"

        return f"{self.basename}: {status}"
