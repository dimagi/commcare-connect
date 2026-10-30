from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

SIM_DOMAIN = "sim-domain"
# The form receiver needs exactly one active opportunity per (domain, app id), so every simulated opportunity
# gets its own app ids. The HQ stand-in recognises them by prefix and treats any other id as an app with no build.
SIM_LEARN_APP_PREFIX = "sim-learn-"
SIM_DELIVER_APP_PREFIX = "sim-deliver-"

LEARN_MODULE_SLUGS = ("module_1", "module_2", "module_3")
ASSESSMENT_SLUG = "demo"
DELIVER_UNIT_SLUGS = ("household_visit", "child_screening")
