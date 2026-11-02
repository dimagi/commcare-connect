"""Standard scenarios. Each module in this package is one scenario, named after the module, and defines:

- ``ORG_NAMES``: the organizations it creates, which ``run_scenario`` deletes before rebuilding them;
- ``build(sim, *, small)``: the steps, where ``small`` asks for the fewest records that still show every state.

Modules whose names start with an underscore hold what the scenarios share and are not scenarios.

Personal scenarios are written the same way in ``local/``, which git ignores. ``run_scenario`` runs them too, but
CI builds only the standard ones.
"""

import importlib
import pkgutil

from . import local


def scenario_names():
    names = standard_scenario_names() + local_scenario_names()
    clashes = {name for name in names if names.count(name) > 1}
    if clashes:
        raise LookupError(f"Personal scenarios in scenarios/local/ reuse standard names: {', '.join(sorted(clashes))}")
    return sorted(names)


def standard_scenario_names():
    return _module_names(__path__)


def local_scenario_names():
    return _module_names(local.__path__)


def get_scenario(name):
    if name in standard_scenario_names():
        return importlib.import_module(f"{__name__}.{name}")
    if name in local_scenario_names():
        return importlib.import_module(f"{local.__name__}.{name}")
    raise LookupError(f"Unknown scenario {name!r}. Available: {', '.join(scenario_names()) or 'none'}")


def _module_names(path):
    return [
        module.name for module in pkgutil.iter_modules(path) if not module.ispkg and not module.name.startswith("_")
    ]
