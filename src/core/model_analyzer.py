from __future__ import annotations

"""
Model Analyzer

Responsible for:
- Scanning vendor SPICE model files (.lib, .cir, .sub, etc.)
- Detecting non-standard constructs (PSpice, LTspice, encrypted, digital, ...)
- Classifying compatibility:
    - standard SPICE (ngspice-friendly)
    - PSpice-like (Xyce preferred)
    - LTspice-like (Xyce preferred)
    - encrypted / unsupported
- Producing ModelMetadata objects used by the rest of the app.

This is deliberately conservative: when in doubt, it marks the model as
needing Xyce or being unsupported. You can relax or refine these rules over time.
"""

import os
import re
from typing import Optional

from .model_metadata import ModelFeatureFlags, ModelMetadata


# --- Regex patterns for feature detection ------------------------------------

# PSpice A-device lines: start with 'A' followed by name and nodes/params
A_DEVICE_RE = re.compile(r"^\s*A[A-Za-z0-9_]+\s", re.IGNORECASE)

# TABLE models (PSpice style)
TABLE_RE = re.compile(r"\bTABLE\s*\(", re.IGNORECASE)

# Basic PSpice-specific behavioral functions
PSPICE_FUNC_RE = re.compile(
    r"\b(LIMIT|ULIM|LLIM|UPLIM|DNLIM|IF|THEN|ELSE)\s*\(",
    re.IGNORECASE,
)

# LTspice-specific behavioral functions / operators (partial list)
LTSPICE_FUNC_RE = re.compile(
    r"\b(ddt|idt|white|pink|round|ceil|floor)\s*\(",
    re.IGNORECASE,
)

# Encryption / protection markers
ENCRYPT_RE = re.compile(
    r"\.(encrypt|protect)\b|encrypted",
    re.IGNORECASE,
)

# Digital / mixed-signal hints (very rough, you can refine)
DIGITAL_HINT_RE = re.compile(
    r"\b(VSWITCH|SW|DIGITAL|AtoD|DtoA)\b",
    re.IGNORECASE,
)

# .SUBCKT line to extract model names
SUBCKT_RE = re.compile(
    r"^\s*\.SUBCKT\s+([A-Za-z0-9_]+)",
    re.IGNORECASE,
)

# .MODEL line to extract model names
MODEL_RE = re.compile(
    r"^\s*\.MODEL\s+([A-Za-z0-9_]+)",
    re.IGNORECASE,
)

# Vendor hints inside comments
TI_HINT_RE = re.compile(r"texas instruments|ti\s+opamp", re.IGNORECASE)
ADI_HINT_RE = re.compile(r"analog devices|adi\s+opamp", re.IGNORECASE)
LT_HINT_RE = re.compile(r"linear technology|ltspice", re.IGNORECASE)


def _detect_features(text: str) -> ModelFeatureFlags:
    """
    Inspect raw SPICE text and return feature flags.

    This is intentionally heuristic and conservative.
    """
    flags = ModelFeatureFlags()

    for line in text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()

        # Skip empty / comment-only lines quickly
        if not stripped or stripped.startswith(("*", ";")):
            continue

        # A-devices (PSpice)
        if A_DEVICE_RE.match(line):
            flags.has_a_devices = True
            # store the first token after "A"
            tokens = stripped.split()
            if tokens:
                flags.primitives.add(tokens[0])

        # TABLE models
        if TABLE_RE.search(line):
            flags.has_table_models = True
            flags.primitives.add("TABLE")

        # PSpice behavioral functions
        if PSPICE_FUNC_RE.search(line):
            flags.has_pspice_behav = True

        # LTspice behavioral functions
        if LTSPICE_FUNC_RE.search(line):
            flags.has_ltspice_behav = True

        # Encryption / protection
        if ENCRYPT_RE.search(line):
            flags.has_encryption = True

        # Very rough digital primitive hints
        if DIGITAL_HINT_RE.search(line):
            flags.has_digital_primitives = True

        # ngspice .control / .endc blocks
        if lower.startswith(".control") or lower.startswith(".endc"):
            flags.has_control_blocks = True

    return flags


def _guess_vendor(text: str) -> Optional[str]:
    """
    Guess vendor from comments / header lines. Purely heuristic.
    """
    # Check only first ~200 lines to avoid scanning huge models
    lines = text.splitlines()[:200]
    joined = "\n".join(lines)

    if TI_HINT_RE.search(joined):
        return "TI"
    if ADI_HINT_RE.search(joined):
        return "ADI"
    if LT_HINT_RE.search(joined):
        return "LTspice/Linear"

    return None


def _extract_model_names(text: str) -> list[str]:
    """
    Extract model names from both .SUBCKT and .MODEL statements in the model file.

    Returns a merged unique list with .SUBCKT names first (in file order),
    followed by .MODEL names (in file order) that aren't duplicates.

    Example lines:
        .SUBCKT OP284 1 2 3 4
        .subckt TL072 IN+ IN- V+ V-
        .MODEL NMOS_MODEL NMOS (VTO=0.5)
        .model PMOS_MODEL PMOS
    """
    subckt_names: list[str] = []
    model_names: list[str] = []
    seen: set[str] = set()
    
    for line in text.splitlines():
        # Extract .SUBCKT names first
        m = SUBCKT_RE.match(line)
        if m:
            name = m.group(1)
            if name not in seen:
                subckt_names.append(name)
                seen.add(name)
        
        # Extract .MODEL names
        m = MODEL_RE.match(line)
        if m:
            name = m.group(1)
            if name not in seen:
                model_names.append(name)
                seen.add(name)
    
    # Return .SUBCKT names first, then .MODEL names
    return subckt_names + model_names


def _classify_from_flags(
    path: str,
    flags: ModelFeatureFlags,
    vendor: Optional[str],
    model_names: list[str],
) -> ModelMetadata:
    """
    Map feature flags to a ModelMetadata classification.

    This function encodes the policy:
    - if encrypted -> unsupported
    - if only standard SPICE -> ngspice is fine
    - if PSpice / LTspice behaviors detected -> Xyce preferred
    """
    basename = os.path.basename(path)
    meta = ModelMetadata(
        path=path,
        basename=basename,
        vendor=vendor,
        model_names=model_names,
        features=flags,
    )

    # 1) Encrypted / protected models are treated as unsupported
    if flags.has_encryption:
        meta.is_encrypted = True
        meta.is_standard_spice = False
        meta.is_pspice = False
        meta.is_ltspice = False

        meta.supports_ngspice = False
        meta.supports_xyce = False
        meta.recommended_simulator = "none"
        meta.conversion_needed = False
        meta.conversion_warnings.append(
            "Model appears to be encrypted/protected. "
            "Exact simulation is not possible."
        )
        return meta

    # 2) Plain SPICE (SPICE3-ish) – no nonstandard features
    if not flags.any_nonstandard():
        meta.is_standard_spice = True
        meta.is_pspice = False
        meta.is_ltspice = False
        meta.is_encrypted = False

        meta.supports_ngspice = True
        meta.supports_xyce = True   # Xyce can run plain SPICE too
        meta.recommended_simulator = "ngspice"
        meta.conversion_needed = False
        return meta

    # 3) If we have A-devices or TABLE or PSpice-specific functions -> PSpice-like
    if flags.has_a_devices or flags.has_table_models or flags.has_pspice_behav:
        meta.is_standard_spice = False
        meta.is_pspice = True
        meta.is_ltspice = False

        # Xyce handles many PSpice features; ngspice likely to choke.
        meta.supports_ngspice = False
        meta.supports_xyce = True
        meta.recommended_simulator = "xyce"
        meta.conversion_needed = False  # you can toggle this when you implement conversion
        meta.required_features.update(flags.primitives)
        return meta

    # 4) If we have LTspice-style behavioral functions -> LTspice-like
    if flags.has_ltspice_behav:
        meta.is_standard_spice = False
        meta.is_pspice = False
        meta.is_ltspice = True

        # Xyce can run many LTspice behav models but not all.
        meta.supports_ngspice = False
        meta.supports_xyce = True   # "best effort"
        meta.recommended_simulator = "xyce"
        meta.conversion_needed = True
        meta.required_features.update(flags.primitives)
        meta.conversion_warnings.append(
            "Model uses LTspice-specific behavioral functions. "
            "Xyce may run it, but approximations/conversion may be needed."
        )
        return meta

    # 5) Digital / mixed-signal hints: treat as non-standard, prefer Xyce
    if flags.has_digital_primitives:
        meta.is_standard_spice = False
        meta.is_pspice = True  # loosely treat as PSpice-like
        meta.is_ltspice = False

        meta.supports_ngspice = False
        meta.supports_xyce = True
        meta.recommended_simulator = "xyce"
        meta.conversion_needed = True
        meta.required_features.update(flags.primitives)
        meta.conversion_warnings.append(
            "Model appears to contain digital or mixed-signal primitives. "
            "Only partial support may be available."
        )
        return meta

    # 6) Fallback: non-standard but not clearly mapped -> prefer Xyce
    meta.is_standard_spice = False
    meta.is_pspice = True
    meta.is_ltspice = False

    meta.supports_ngspice = False
    meta.supports_xyce = True
    meta.recommended_simulator = "xyce"
    meta.conversion_needed = True
    meta.conversion_warnings.append(
        "Model contains non-standard constructs; Xyce is recommended. "
        "Conversion or simplification may improve robustness."
    )
    return meta


def analyze_model(path: str) -> ModelMetadata:
    """
    High-level API used by the rest of the app.

    Example usage:

        from core.model_analyzer import analyze_model

        meta = analyze_model("/path/to/OP284.lib")
        print(meta.short_summary())
        # -> "OP284.lib: PSpice-like (prefers xyce)"

    The returned ModelMetadata can be:
    - shown in the GUI (vendor, supported simulators, warnings)
    - stored in a local model database
    - used to decide which simulator backend to route to
    """
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError as exc:
        # On error, mark as unsupported
        basename = os.path.basename(path)
        meta = ModelMetadata(
            path=path,
            basename=basename,
            vendor=None,
            model_names=[],
            features=ModelFeatureFlags(),
        )
        meta.recommended_simulator = "none"
        meta.conversion_warnings.append(
            f"Could not read model file: {exc}"
        )
        return meta

    flags = _detect_features(text)
    vendor = _guess_vendor(text)
    model_names = _extract_model_names(text)

    meta = _classify_from_flags(path, flags, vendor, model_names)
    return meta
