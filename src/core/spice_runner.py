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


def run_spice_dc_analysis(netlist: str) -> Dict[str, float]:
    """
    Run ngspice DC operating point analysis (.op) and return nodal voltages.
    
    Returns:
        Dictionary mapping node names to DC voltages (in volts).
        Example: {"0": 0.0, "Vin": 5.0, "Vout": 2.5, ...}
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        netlist_path = tmpdir_path / "circuit_dc.cir"
        log_path = tmpdir_path / "ngspice.log"
        
        # Write the netlist
        netlist_path.write_text(netlist, encoding="utf-8")
        
        # Call ngspice in batch mode
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
        
        # Read the log
        log_text = ""
        if log_path.exists():
            log_text = log_path.read_text(encoding="utf-8", errors="ignore")
        
        if result.returncode != 0:
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
        
        # Parse the .op output
        # ngspice .op output format:
        #   Node                                    Voltage
        #   ----                                    -------
        #   0                                       0.000000
        #   Vin                                     5.000000
        #   Vout                                    2.500000
        #   ...
        
        nodal_voltages: Dict[str, float] = {}
        
        # Look for the voltage table
        in_voltage_table = False
        for line in log_text.splitlines():
            # Check if we're entering the voltage table
            if "Node" in line and "Voltage" in line:
                in_voltage_table = True
                continue
            
            # Skip separator line
            if in_voltage_table and "----" in line:
                continue
            
            # Parse voltage lines
            if in_voltage_table:
                # Skip separator lines and empty lines
                if not line.strip() or "----" in line:
                    continue
                
                # Check if we've reached the end of the voltage table
                # (look for common patterns that indicate end of table)
                if any(marker in line.lower() for marker in ["total", "analysis time", "elapsed time"]):
                    # Skip these summary lines - they're not node voltages
                    continue
                
                # Split by whitespace, but node names might have spaces
                # Format: "node_name    voltage_value"
                parts = line.split()
                if len(parts) >= 2:
                    # Last part should be the voltage
                    try:
                        voltage = float(parts[-1])
                        # Everything before the last part is the node name
                        node_name = " ".join(parts[:-1]).strip()
                        
                        # Filter out invalid node names (internal SPICE parameters)
                        # Valid node names typically:
                        # - Are short (not sentences)
                        # - Don't contain common parameter keywords
                        # - Are not empty
                        if node_name and len(node_name) < 50:  # Reasonable node name length
                            # Skip entries that look like parameters (contain common keywords)
                            param_keywords = ["resistance", "ac", "dc", "dtemp", "bv_max", "noisy", 
                                            "phase", "freq", "portnum", "z0", "acmag", "i", "p",
                                            "total", "analysis", "time", "seconds", "elapsed",
                                            "rsh", "narrow", "short", "tc1", "tc2", "tce", "defw",
                                            "l", "kf", "af", "r", "lf", "wf", "ef"]
                            
                            node_lower = node_name.lower()
                            # Check if this looks like a parameter (contains keyword + value)
                            is_parameter = any(keyword in node_lower for keyword in param_keywords)
                            
                            # Also skip if it has #branch (current through voltage source)
                            if "#branch" in node_lower:
                                is_parameter = True
                            
                            if not is_parameter:
                                nodal_voltages[node_name] = voltage
                    except ValueError:
                        # Not a valid voltage line, might be end of table
                        pass
        
        if not nodal_voltages:
            raise SpiceError(
                "Could not parse nodal voltages from ngspice output.\n"
                "Raw log:\n" + log_text
            )
        
        return nodal_voltages