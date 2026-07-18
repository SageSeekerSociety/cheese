"""Scenario registry: every module in this package that defines a top-level
`scenario` (a Scenario instance) is auto-registered — add a file, get a case."""

import importlib
import pkgutil

from evals.lib.records import Scenario


def discover() -> dict[str, Scenario]:
    """Scenario id → Scenario, discovered from the package's modules."""
    registry: dict[str, Scenario] = {}
    for module_info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        scenario = getattr(module, "scenario", None)
        if isinstance(scenario, Scenario):
            if scenario.id in registry:
                raise ValueError(f"duplicate scenario id {scenario.id!r}")
            registry[scenario.id] = scenario
    return dict(sorted(registry.items()))
