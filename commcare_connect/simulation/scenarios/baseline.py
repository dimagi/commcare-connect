"""One active opportunity whose workers have finished learning and claimed it; the starting point for most work."""

from . import _common

TITLE = "Baseline"
ORG_NAMES = _common.org_names(TITLE)


def build(sim, *, small):
    _common.staffed_opportunity(sim, TITLE, 2 if small else 8)
