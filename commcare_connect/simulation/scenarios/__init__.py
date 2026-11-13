"""Standard scenarios. Each module in this package is one scenario, named after the module, and defines:

- ``ORG_NAMES``: the organizations it creates, which ``run_scenario`` deletes before rebuilding them;
- ``build(sim, *, small)``: the steps, where ``small`` asks for the fewest records that still show every state.
"""

import importlib
import pkgutil


def scenario_names():
    return sorted(module.name for module in pkgutil.iter_modules(__path__))


def get_scenario(name):
    if name not in scenario_names():
        raise LookupError(f"Unknown scenario {name!r}. Available: {', '.join(scenario_names()) or 'none'}")
    return importlib.import_module(f"{__name__}.{name}")
