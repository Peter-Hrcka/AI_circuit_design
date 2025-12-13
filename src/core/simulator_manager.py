from __future__ import annotations

"""
SimulatorManager

Central place that decides *which* SPICE backend to use for a given
simulation, based on ModelMetadata (from model_analyzer).

Right now:
- Only NgSpiceBackend is implemented, and it is the default.

Later:
- Add XyceBackend and route models classified as PSpice/LTspice-like to it.
"""

from typing import Dict, Optional, Tuple

from .model_metadata import ModelMetadata
from .simulator_backend import ISpiceBackend, NgSpiceBackend
from .spice_runner import SpiceError
from .xyce_backend import XyceBackend
from .circuit import Circuit



class SimulatorManager:
    """
    Owns the available simulator backends and chooses one for each job.
    """

    def __init__(self) -> None:
        self._backends: Dict[str, ISpiceBackend] = {}

        # Register known backends
        self.register_backend(NgSpiceBackend())
        self.register_backend(XyceBackend())

        # Placeholder: later you will add something like:
        #   self.register_backend(XyceBackend())
        # and the manager will be able to route models to it.

    # --------------------------------------------------------------------- #
    # Backend registry
    # --------------------------------------------------------------------- #

    def register_backend(self, backend: ISpiceBackend) -> None:
        self._backends[backend.name] = backend

    def get_backend(self, name: str) -> Optional[ISpiceBackend]:
        return self._backends.get(name)

    # --------------------------------------------------------------------- #
    # Core routing
    # --------------------------------------------------------------------- #

    def _choose_backend(self, meta: Optional[ModelMetadata], pspice_compat_needed: bool = False) -> Tuple[ISpiceBackend, str]:
        """
        Decide which backend to use, given optional ModelMetadata.
        
        If pspice_compat_needed is True and user wants ngspice, we may still route to ngspice
        even if the model recommends Xyce (user override via checkbox).

        Policy for now:
        - if meta is None -> use "ngspice"
        - else:
            - if meta.recommended_simulator is a registered backend -> use that
            - else if "ngspice" is registered and meta.supports_ngspice -> use ngspice
            - else -> fall back to ngspice if present, otherwise error
        
        Returns:
            Tuple of (backend, backend_name)
        """
        # 1) No metadata: default backend
        if meta is None:
            backend = self._backends.get("ngspice")
            if backend is None:
                raise RuntimeError("No ngspice backend registered.")
            return backend, "ngspice"

        # 2) If user enabled PSpice compat checkbox, prefer ngspice even if model recommends Xyce
        # (user override)
        if pspice_compat_needed and "ngspice" in self._backends:
            return self._backends["ngspice"], "ngspice"

        # 3) Use recommended simulator if we have it
        preferred = meta.recommended_simulator
        if preferred in self._backends:
            return self._backends[preferred], preferred

        # 4) Fallback: try ngspice if supported and available
        if meta.supports_ngspice and "ngspice" in self._backends:
            return self._backends["ngspice"], "ngspice"

        # 5) Last resort: any backend we have
        if self._backends:
            backend = next(iter(self._backends.values()))
            return backend, backend.name

        raise RuntimeError("No simulator backends are registered.")

    # --------------------------------------------------------------------- #
    # High-level convenience methods used by the rest of the app
    # --------------------------------------------------------------------- #

    def run_ac_gain(
        self,
        netlist: str,
        meta: Optional[ModelMetadata] = None,
        circuit: Optional[Circuit] = None,
    ) -> Dict[str, float]:
        pspice_compat_needed = self._compute_pspice_compat_needed(circuit)
        backend, _ = self._choose_backend(meta, pspice_compat_needed)
        return backend.run_ac_gain(netlist)

    def run_ac_sweep(
        self,
        netlist: str,
        meta: Optional[ModelMetadata] = None,
        circuit: Optional[Circuit] = None,
    ) -> Dict[str, list]:
        pspice_compat_needed = self._compute_pspice_compat_needed(circuit)
        backend, _ = self._choose_backend(meta, pspice_compat_needed)
        return backend.run_ac_sweep(netlist)

    def run_noise_sweep(
        self,
        netlist: str,
        meta: Optional[ModelMetadata] = None,
        circuit: Optional[Circuit] = None,
    ) -> Dict[str, list | float]:
        pspice_compat_needed = self._compute_pspice_compat_needed(circuit)
        backend, _ = self._choose_backend(meta, pspice_compat_needed)
        return backend.run_noise_sweep(netlist)
    
    def run_dc_analysis(
        self,
        netlist: str,
        meta: Optional[ModelMetadata] = None,
        circuit: Optional[Circuit] = None,
    ) -> Dict[str, float]:
        pspice_compat_needed = self._compute_pspice_compat_needed(circuit)
        backend, _ = self._choose_backend(meta, pspice_compat_needed)
        return backend.run_dc_analysis(netlist)
    
    def _compute_pspice_compat_needed(self, circuit: Optional[Circuit]) -> bool:
        """
        Compute whether PSpice compatibility is needed based on circuit components.
        
        Returns:
            True if any component has ngspice_pspice_compat set to True
        """
        if circuit is None:
            return False
        
        for comp in circuit.components:
            if comp.extra.get("ngspice_pspice_compat", False):
                return True
        return False
    
    def get_simulation_context(
        self,
        meta_original: Optional[ModelMetadata],
        meta_converted: Optional[ModelMetadata],
        circuit: Optional[Circuit],
    ) -> Tuple[str, bool, str, bool]:
        """
        Compute simulation context information.
        
        Args:
            meta_original: Original model metadata (before conversion)
            meta_converted: Converted model metadata (after conversion, if any)
            circuit: Circuit object to check for PSpice compatibility flags
        
        Returns:
            Tuple of (simulator_name, conversion_used, run_mode, ngspice_pspice_compat)
        """
        pspice_compat_needed = self._compute_pspice_compat_needed(circuit)
        
        # Determine if conversion was used
        conversion_used = False
        if meta_original is not None and meta_converted is not None:
            conversion_used = (meta_converted.path != meta_original.path)
        
        # Determine run mode
        if conversion_used:
            run_mode = "converted_model"
        else:
            run_mode = "original_model"
        
        # Choose backend to determine simulator name
        backend, simulator_name = self._choose_backend(meta_converted or meta_original, pspice_compat_needed)
        
        return simulator_name, conversion_used, run_mode, pspice_compat_needed


# A default global manager instance that you can import and reuse.
default_simulator_manager = SimulatorManager()
