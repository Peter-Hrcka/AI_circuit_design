from __future__ import annotations

"""
Simulator backend abstractions.

- ISpiceBackend: interface that any SPICE-like simulator must implement.
- NgSpiceBackend: concrete implementation that wraps `spice_runner.py`.

Later you can add:
- XyceBackend
- Other proprietary backends
"""

from abc import ABC, abstractmethod
from typing import Dict

from .spice_runner import (
    run_spice_ac_gain,
    run_spice_ac_sweep,
    run_spice_noise_sweep,
    SpiceError,
)


class ISpiceBackend(ABC):
    """
    Common interface for all SPICE backends (ngspice, Xyce, ...).

    The rest of the app should depend on this interface, not on a specific
    simulator implementation.
    """

    name: str

    @abstractmethod
    def run_ac_gain(self, netlist: str) -> Dict[str, float]:
        """
        Single-frequency AC gain:
        - netlist must contain one `.ac` point and `.print ac vm(Vout) vm(Vin)`
        - returns e.g. {"gain_db": ..., "vm_vout": ..., "vm_vin": ...}
        """
        raise NotImplementedError

    @abstractmethod
    def run_ac_sweep(self, netlist: str) -> Dict[str, list]:
        """
        Multi-point AC sweep:
        - netlist must contain `.ac ... f_start f_stop` and `.print ac ...`
        - returns e.g. {"freq_hz": [...], "vm_vout": [...], "gain_db": [...]}
        """
        raise NotImplementedError

    @abstractmethod
    def run_noise_sweep(self, netlist: str) -> Dict[str, list | float]:
        """
        Noise analysis:
        - netlist must contain `.noise` and `.print noise ...`
        - returns e.g. {
              "freq_hz": [...],
              "onoise_total": [...],
              "inoise_total": [...],
              "total_onoise_rms": float,
              "total_inoise_rms": float,
          }
        """
        raise NotImplementedError


class NgSpiceBackend(ISpiceBackend):
    """
    Concrete backend using ngspice via `spice_runner.py`.

    This is your current working implementation, just wrapped into
    an object that conforms to ISpiceBackend.
    """

    name = "ngspice"

    def run_ac_gain(self, netlist: str) -> Dict[str, float]:
        return run_spice_ac_gain(netlist)

    def run_ac_sweep(self, netlist: str) -> Dict[str, list]:
        return run_spice_ac_sweep(netlist)

    def run_noise_sweep(self, netlist: str) -> Dict[str, list | float]:
        return run_spice_noise_sweep(netlist)
