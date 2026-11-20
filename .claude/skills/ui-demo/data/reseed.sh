#!/bin/bash
# reseed.sh [scenario] — reset demo data (org, users, invites). Run from the task folder; idempotent, so run it before
# every take. Feature data comes from a simulation scenario (commcare_connect/simulation) built with Asha as admin,
# and, for areas the simulation has no steps for yet, from the task folder's own seed_extra.py.
set -uo pipefail
DATA_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$(cd "$DATA_DIR/../server" && pwd)"  # record_settings, shared with serve.sh
TASK_DIR="$PWD"
REPO="$(git -C "$DATA_DIR" rev-parse --show-toplevel)"
cd "$REPO" || exit 1
# shellcheck disable=SC1091
source .venv/bin/activate || { echo "no .venv in $REPO — run: uv sync"; exit 1; }
export PYTHONPATH="$SERVER_DIR" DJANGO_SETTINGS_MODULE=record_settings
./manage.py shell < "$DATA_DIR/seed.py" 2>&1 | tail -1 || { echo "seed.py failed"; exit 1; }
# --force: run_scenario refuses to run with DEBUG off, which record_settings sets. Email = USERS.admin in connect.mjs.
[ -z "${1:-}" ] || ./manage.py run_scenario "$1" --admin-email asha.admin@childhealth.org --force
[ ! -f "$TASK_DIR/seed_extra.py" ] || ./manage.py shell < "$TASK_DIR/seed_extra.py" 2>&1 | tail -1 || { echo "seed_extra.py failed"; exit 1; }
