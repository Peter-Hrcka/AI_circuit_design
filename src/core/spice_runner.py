"""
Interface to the SPICE engine (ngspice / Xyce).

This version:
- writes a temporary netlist file
- calls ngspice in batch mode
- parses the AC .print output to get gain in dB
"""

from __future__ import annotations
from typing import Dict

import math
import subprocess
import tempfile
from pathlib import Path
import re
import platform


# Decide which executable name to use.
# On Windows we prefer the console version 'ngspice_con'.
if platform.system() == "Windows":
    NGSPICE_EXECUTABLE = "ngspice_con"
else:
    NGSPICE_EXECUTABLE = "ngspice"


class SpiceError(RuntimeError):
    """Custom exception for SPICE-related errors."""
    pass


def run_spice_ac_gain(netlist: str) -> Dict[str, float]:
    """
    Run ngspice on the given netlist (AC analysis with
    `.print ac vm(Vout) vm(Vin)`) and return a dict with the
    gain in dB and the raw magnitudes.

    Assumes:
    - The AC source at Vin has magnitude 1 V (as in build_non_inverting_ac_netlist),
      so |Vout| = linear gain.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        netlist_path = tmpdir_path / "circuit_ac.cir"
        log_path = tmpdir_path / "ngspice.log"

        # 1) Write the netlist
        netlist_path.write_text(netlist, encoding="utf-8")

        # 2) Call ngspice in batch mode
        try:
            result = subprocess.run(
                [NGSPICE_EXECUTABLE, "-b", "-o", str(log_path), str(netlist_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SpiceError(
                f"{NGSPICE_EXECUTABLE} executable not found. Make sure it is "
                "installed and available on your PATH."
            ) from exc

        # 3) Read the log (if any), even on error
        log_text = ""
        if log_path.exists():
            log_text = log_path.read_text(encoding="utf-8", errors="ignore")

        if result.returncode != 0:
            # Include netlist + log in the error to see the real SPICE problem
            raise SpiceError(
                "ngspice failed with return code "
                f"{result.returncode}.\n\n"
                "=== NETLIST SENT TO NGSPICE ===\n"
                f"{netlist}\n\n"
                "=== NGSPICE STDOUT ===\n"
                f"{result.stdout}\n\n"
                "=== NGSPICE STDERR ===\n"
                f"{result.stderr}\n\n"
                "=== NGSPICE LOG ===\n"
                f"{log_text}"
            )

        if not log_path.exists():
            raise SpiceError("ngspice log file was not created.")

        # 4) Parse the .print output lines.
        #
        # Typical .print ac output:
        #   index   freq          vm(vout)     vm(vin)
        #   0       1.000E+03     1.000E+02    1.000E+00
        #
        # So we expect FOUR numeric columns: index, freq, vm(vout), vm(vin).
        float_line_regex = re.compile(
            r"^\s*([0-9.eE+\-]+)\s+([0-9.eE+\-]+)\s+([0-9.eE+\-]+)\s+([0-9.eE+\-]+)"
        )

        vm_vout = None
        vm_vin = None

        for line in log_text.splitlines():
            match = float_line_regex.match(line)
            if match:
                _idx_str, _freq_str, vout_str, vin_str = match.groups()
                try:
                    vm_vout = float(vout_str)
                    vm_vin = float(vin_str)
                except ValueError:
                    continue

        if vm_vout is None:
            raise SpiceError(
                "Could not parse vm(Vout) from ngspice output.\n"
                "Raw log:\n" + log_text
            )

        if vm_vout <= 0.0:
            raise SpiceError(
                f"Non-positive Vout magnitude ({vm_vout}) in SPICE output; "
                "cannot compute gain in dB."
            )

        # With AC source magnitude = 1 V, |Vout| = gain.
        gain_linear = vm_vout
        gain_db = 20.0 * math.log10(gain_linear)

        return {
            "gain_db": gain_db,
            "vm_vout": vm_vout,
            "vm_vin": vm_vin if vm_vin is not None else 0.0,
        }

def run_spice_ac_sweep(netlist: str) -> Dict[str, list]:
    """
    Run ngspice on an AC sweep netlist (multiple frequency points).

    Returns:
        {
            "freq_hz": [...],
            "vm_vout": [...],
            "vm_vin": [...],   # may be all 1.0 if not printed
            "gain_db": [...],
        }
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        netlist_path = tmpdir_path / "ac_sweep.cir"
        log_path = tmpdir_path / "ac_sweep.log"

        netlist_path.write_text(netlist, encoding="utf-8")

        try:
            result = subprocess.run(
                [NGSPICE_EXECUTABLE, "-b", "-o", str(log_path), str(netlist_path)],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise SpiceError(
                f"{NGSPICE_EXECUTABLE} not found on PATH."
            ) from exc

        if result.returncode != 0:
            raise SpiceError(
                f"ngspice returned error:\n{result.stderr}"
            )

        if not log_path.exists():
            raise SpiceError("SPICE log missing.")

        log_text = log_path.read_text(encoding="utf-8", errors="ignore")

        freq = []
        vout = []
        vin = []

        for line in log_text.splitlines():
            parts = line.split()
            # Require at least index + freq + vout
            if len(parts) < 3:
                continue
            try:
                floats = [float(p) for p in parts]
            except ValueError:
                continue

            if len(floats) >= 4:
                # index, freq, vout, vin (and maybe more we ignore)
                _idx, f, vo, vi = floats[:4]
            elif len(floats) == 3:
                # index, freq, vout   (Vin assumed 1.0 for AC=1 source)
                _idx, f, vo = floats
                vi = 1.0
            else:
                # shouldn't happen, but be safe
                continue

            freq.append(f)
            vout.append(vo)
            vin.append(vi)

        if len(freq) == 0:
            raise SpiceError("Could not parse AC sweep results.")

        gain_db = [20.0 * math.log10(abs(vo) if vo != 0 else 1e-30) for vo in vout]

        return {
            "freq_hz": freq,
            "vm_vout": vout,
            "vm_vin": vin,
            "gain_db": gain_db,
        }

def run_spice_noise_sweep(netlist: str) -> Dict[str, float]:
    """
    Run ngspice on a noise netlist that contains a .control block:

        .control
          noise V(Vout) V1 dec <points> <f_start> <f_stop>
          setplot noise2
          print onoise_total inoise_total
          quit
        .endc

    Returns:
        {
            "total_onoise_rms": float,   # output noise over band
            "total_inoise_rms": float,   # input-referred noise over band
        }
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        netlist_path = tmpdir_path / "noise.cir"
        log_path = tmpdir_path / "noise.log"

        netlist_path.write_text(netlist, encoding="utf-8")

        try:
            result = subprocess.run(
                [NGSPICE_EXECUTABLE, "-b", "-o", str(log_path), str(netlist_path)],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise SpiceError(f"{NGSPICE_EXECUTABLE} not found on PATH.") from exc

        log_text = ""
        if log_path.exists():
            log_text = log_path.read_text(encoding="utf-8", errors="ignore")

        if result.returncode != 0:
            raise SpiceError(
                "ngspice failed in noise analysis with return code "
                f"{result.returncode}.\n\n"
                "=== NETLIST SENT TO NGSPICE ===\n"
                f"{netlist}\n\n"
                "=== NGSPICE STDOUT ===\n"
                f"{result.stdout}\n\n"
                "=== NGSPICE STDERR ===\n"
                f"{result.stderr}\n\n"
                "=== NGSPICE LOG ===\n"
                f"{log_text}"
            )

        if not log_path.exists():
            raise SpiceError("SPICE log missing.")

        # Parse 'onoise_total = ...' and 'inoise_total = ...' from the log
        onoise_total = None
        inoise_total = None

        for line in log_text.splitlines():
            line_stripped = line.strip()
            if line_stripped.lower().startswith("onoise_total"):
                # Expected format: onoise_total = <value>
                parts = line_stripped.replace("=", " ").split()
                try:
                    onoise_total = float(parts[-1])
                except (ValueError, IndexError):
                    pass
            elif line_stripped.lower().startswith("inoise_total"):
                parts = line_stripped.replace("=", " ").split()
                try:
                    inoise_total = float(parts[-1])
                except (ValueError, IndexError):
                    pass

        if onoise_total is None or inoise_total is None:
            raise SpiceError(
                "Could not parse onoise_total/inoise_total from noise analysis log."
            )

        return {
            "total_onoise_rms": float(onoise_total),
            "total_inoise_rms": float(inoise_total),
        }
