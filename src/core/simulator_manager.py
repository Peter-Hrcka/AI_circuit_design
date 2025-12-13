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

from typing import Dict, Optional, Tuple, Any

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
        diagnostics_out: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, float]:
        """
        Run AC gain analysis with automatic fallback to Xyce on ngspice MIF errors.
        
        Args:
            netlist: SPICE netlist
            meta: Optional model metadata
            circuit: Optional circuit object
            diagnostics_out: Optional dict to receive diagnostics (initial_backend, fallback_occurred, final_backend)
        
        Returns:
            Results dictionary
        """
        pspice_compat_needed = self._compute_pspice_compat_needed(circuit)
        result, diagnostics = self._run_with_fallback(
            netlist, meta, circuit, "run_ac_gain", pspice_compat=pspice_compat_needed
        )
        if diagnostics_out is not None:
            diagnostics_out.update(diagnostics)
        return result

    def run_ac_sweep(
        self,
        netlist: str,
        meta: Optional[ModelMetadata] = None,
        circuit: Optional[Circuit] = None,
        diagnostics_out: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, list]:
        """
        Run AC sweep analysis with automatic fallback to Xyce on ngspice MIF errors.
        
        Args:
            netlist: SPICE netlist
            meta: Optional model metadata
            circuit: Optional circuit object
            diagnostics_out: Optional dict to receive diagnostics (initial_backend, fallback_occurred, final_backend)
        
        Returns:
            Results dictionary
        """
        pspice_compat_needed = self._compute_pspice_compat_needed(circuit)
        result, diagnostics = self._run_with_fallback(
            netlist, meta, circuit, "run_ac_sweep", pspice_compat=pspice_compat_needed
        )
        if diagnostics_out is not None:
            diagnostics_out.update(diagnostics)
        return result

    def run_noise_sweep(
        self,
        netlist: str,
        meta: Optional[ModelMetadata] = None,
        circuit: Optional[Circuit] = None,
        diagnostics_out: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, list | float]:
        """
        Run noise sweep analysis with automatic fallback to Xyce on ngspice MIF errors.
        
        Args:
            netlist: SPICE netlist
            meta: Optional model metadata
            circuit: Optional circuit object
            diagnostics_out: Optional dict to receive diagnostics (initial_backend, fallback_occurred, final_backend)
        
        Returns:
            Results dictionary
        """
        pspice_compat_needed = self._compute_pspice_compat_needed(circuit)
        result, diagnostics = self._run_with_fallback(
            netlist, meta, circuit, "run_noise_sweep", pspice_compat=pspice_compat_needed
        )
        if diagnostics_out is not None:
            diagnostics_out.update(diagnostics)
        return result
    
    def run_dc_analysis(
        self,
        netlist: str,
        meta: Optional[ModelMetadata] = None,
        circuit: Optional[Circuit] = None,
        diagnostics_out: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, float]:
        """
        Run DC analysis with automatic fallback to Xyce on ngspice MIF errors.
        
        Args:
            netlist: SPICE netlist
            meta: Optional model metadata
            circuit: Optional circuit object
            diagnostics_out: Optional dict to receive diagnostics (initial_backend, fallback_occurred, final_backend)
        
        Returns:
            Results dictionary
        """
        pspice_compat_needed = self._compute_pspice_compat_needed(circuit)
        result, diagnostics = self._run_with_fallback(
            netlist, meta, circuit, "run_dc_analysis", pspice_compat=pspice_compat_needed
        )
        if diagnostics_out is not None:
            diagnostics_out.update(diagnostics)
        return result
    
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
    
    def _is_mif_code_model_error(self, error: SpiceError) -> bool:
        """
        Check if a SpiceError indicates a MIF/code-model failure that should trigger
        automatic fallback to Xyce.
        
        Args:
            error: The SpiceError exception to check
        
        Returns:
            True if the error indicates MIF/code-model issues
        """
        error_text = str(error).upper()
        mif_indicators = [
            "MIF-ERROR",
            "UNABLE TO FIND DEFINITION OF MODEL",
            "MODEL TYPE MISMATCH",
            "CODE MODEL",
        ]
        return any(indicator in error_text for indicator in mif_indicators)
    
    def _run_with_fallback(
        self,
        netlist: str,
        meta: Optional[ModelMetadata],
        circuit: Optional[Circuit],
        run_method: str,
        pspice_compat: bool = False,
    ) -> Tuple[Dict, Dict[str, any]]:
        """
        Run a simulation with automatic fallback from ngspice to Xyce on MIF errors.
        
        Args:
            netlist: SPICE netlist to run
            meta: Optional model metadata
            circuit: Optional circuit object
            run_method: Method name to call on backend ("run_ac_gain", "run_ac_sweep", etc.)
            pspice_compat: Whether PSpice compatibility is needed
        
        Returns:
            Tuple of (results_dict, diagnostics_dict)
            diagnostics_dict contains:
                - initial_backend: name of backend tried first
                - fallback_occurred: bool
                - final_backend: name of backend that succeeded
        """
        pspice_compat_needed = self._compute_pspice_compat_needed(circuit)
        initial_backend, initial_backend_name = self._choose_backend(meta, pspice_compat_needed)
        
        diagnostics = {
            "initial_backend": initial_backend_name,
            "fallback_occurred": False,
            "final_backend": initial_backend_name,
        }
        
        try:
            # Try with initial backend
            if run_method == "run_ac_gain":
                result = initial_backend.run_ac_gain(netlist, pspice_compat=pspice_compat)
            elif run_method == "run_ac_sweep":
                result = initial_backend.run_ac_sweep(netlist, pspice_compat=pspice_compat)
            elif run_method == "run_noise_sweep":
                result = initial_backend.run_noise_sweep(netlist, pspice_compat=pspice_compat)
            elif run_method == "run_dc_analysis":
                result = initial_backend.run_dc_analysis(netlist, circuit=circuit, pspice_compat=pspice_compat)
            else:
                raise ValueError(f"Unknown run method: {run_method}")
            
            return result, diagnostics
        
        except SpiceError as exc:
            # Check if this is a MIF/code-model error and we can fallback to Xyce
            if (initial_backend_name == "ngspice" and 
                self._is_mif_code_model_error(exc) and 
                "xyce" in self._backends):
                
                # Retry with Xyce (Xyce ignores pspice_compat parameter)
                xyce_backend = self._backends["xyce"]
                diagnostics["fallback_occurred"] = True
                diagnostics["final_backend"] = "xyce"
                
                try:
                    if run_method == "run_ac_gain":
                        result = xyce_backend.run_ac_gain(netlist, pspice_compat=False)
                    elif run_method == "run_ac_sweep":
                        result = xyce_backend.run_ac_sweep(netlist, pspice_compat=False)
                    elif run_method == "run_noise_sweep":
                        result = xyce_backend.run_noise_sweep(netlist, pspice_compat=False)
                    elif run_method == "run_dc_analysis":
                        result = xyce_backend.run_dc_analysis(netlist, circuit=circuit, pspice_compat=False)
                    else:
                        raise ValueError(f"Unknown run method: {run_method}")
                    
                    return result, diagnostics
                
                except Exception as xyce_exc:
                    # If Xyce also fails, raise the original ngspice error with context
                    raise SpiceError(
                        f"ngspice failed with MIF/code-model error, then Xyce fallback also failed.\n\n"
                        f"Original ngspice error:\n{exc}\n\n"
                        f"Xyce error:\n{xyce_exc}"
                    ) from xyce_exc
            else:
                # Not a fallback case, or fallback not available - re-raise original error
                raise
    
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


def _test_backend_pspice_compat_signatures() -> None:
    """
    Smoke test to ensure both backends accept pspice_compat parameter.
    
    This is a dev helper function to verify that:
    - NgSpiceBackend.run_dc_analysis(netlist, pspice_compat=True) doesn't raise TypeError
    - XyceBackend.run_dc_analysis(netlist, pspice_compat=True) doesn't raise TypeError
    
    Note: This doesn't actually run simulations, just checks method signatures.
    """
    from .simulator_backend import NgSpiceBackend
    from .xyce_backend import XyceBackend
    
    # Minimal netlist for testing
    minimal_netlist = """
* Test netlist
V1 1 0 DC 5
R1 1 0 1k
.op
.end
"""
    
    # Test NgSpiceBackend
    ngspice = NgSpiceBackend()
    try:
        # This will likely fail at runtime (no actual ngspice execution),
        # but should not raise TypeError about missing pspice_compat parameter
        ngspice.run_dc_analysis(minimal_netlist, pspice_compat=True)
    except TypeError as e:
        if "pspice_compat" in str(e):
            raise AssertionError(f"NgSpiceBackend.run_dc_analysis missing pspice_compat parameter: {e}")
        # Other TypeErrors (e.g., from actual execution) are OK
    except Exception:
        # Any other exception (SpiceError, NotImplementedError, etc.) is fine
        # We're only checking that the signature accepts pspice_compat
        pass
    
    # Test XyceBackend
    xyce = XyceBackend()
    try:
        # This will likely raise NotImplementedError (DC not implemented),
        # but should not raise TypeError about missing pspice_compat parameter
        xyce.run_dc_analysis(minimal_netlist, pspice_compat=True)
    except TypeError as e:
        if "pspice_compat" in str(e):
            raise AssertionError(f"XyceBackend.run_dc_analysis missing pspice_compat parameter: {e}")
        # Other TypeErrors are OK
    except NotImplementedError:
        # Expected - DC analysis not implemented for Xyce
        pass
    except Exception:
        # Any other exception is fine - we're only checking the signature
        pass


# A default global manager instance that you can import and reuse.
default_simulator_manager = SimulatorManager()
