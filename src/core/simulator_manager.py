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

from typing import Dict, Optional

from .model_metadata import ModelMetadata
from .simulator_backend import ISpiceBackend, NgSpiceBackend
from .spice_runner import SpiceError
from .xyce_backend import XyceBackend



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

    def _choose_backend(self, meta: Optional[ModelMetadata]) -> ISpiceBackend:
        """
        Decide which backend to use, given optional ModelMetadata.

        Policy for now:
        - if meta is None -> use "ngspice"
        - else:
            - if meta.recommended_simulator is a registered backend -> use that
            - else if "ngspice" is registered and meta.supports_ngspice -> use ngspice
            - else -> fall back to ngspice if present, otherwise error
        """
        # 1) No metadata: default backend
        if meta is None:
            backend = self._backends.get("ngspice")
            if backend is None:
                raise RuntimeError("No ngspice backend registered.")
            return backend

        # 2) Use recommended simulator if we have it
        preferred = meta.recommended_simulator
        if preferred in self._backends:
            return self._backends[preferred]

        # 3) Fallback: try ngspice if supported and available
        if meta.supports_ngspice and "ngspice" in self._backends:
            return self._backends["ngspice"]

        # 4) Last resort: any backend we have
        if self._backends:
            return next(iter(self._backends.values()))

        raise RuntimeError("No simulator backends are registered.")

    # --------------------------------------------------------------------- #
    # High-level convenience methods used by the rest of the app
    # --------------------------------------------------------------------- #

    def run_ac_gain(
        self,
        netlist: str,
        meta: Optional[ModelMetadata] = None,
    ) -> Dict[str, float]:
        backend = self._choose_backend(meta)
        return backend.run_ac_gain(netlist)

    def run_ac_sweep(
        self,
        netlist: str,
        meta: Optional[ModelMetadata] = None,
    ) -> Dict[str, list]:
        backend = self._choose_backend(meta)
        return backend.run_ac_sweep(netlist)

    def run_noise_sweep(
        self,
        netlist: str,
        meta: Optional[ModelMetadata] = None,
    ) -> Dict[str, list | float]:
        backend = self._choose_backend(meta)
        return backend.run_noise_sweep(netlist)


# A default global manager instance that you can import and reuse.
default_simulator_manager = SimulatorManager()
