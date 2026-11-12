#!/usr/bin/env bash
# Set up a git worktree: copy .env from the main checkout, install dependencies, and give the
# worktree its own database and Redis database. worktrunk runs this from .config/wt.toml; with
# another worktree tool, run it yourself in the new worktree.
#
# To use the main checkout's database and Redis database instead, pass --share or set
# WORKTREE_SHARE=1 (`wt new my-branch --share=1` does this). Migrations are then not run, so the
# branch can't change the main checkout's schema by accident.

set -euo pipefail

share=${WORKTREE_SHARE:-0}
if [[ "${1:-}" == "--share" ]]; then
    share=1
fi

main_checkout=$(git worktree list --porcelain | sed -n 's/^worktree //p' | head -n 1)
cd "$(git rev-parse --show-toplevel)"

if [[ "$PWD" == "$main_checkout" ]]; then
    echo "Run this in a worktree, not the main checkout." >&2
    exit 1
fi
if [[ ! -f .env ]]; then
    cp "$main_checkout/.env" .env
fi

uv sync
npm ci --no-audit --no-fund
if [[ "$share" == "1" ]]; then
    uv run python scripts/worktree.py setup --share
else
    uv run python scripts/worktree.py setup
    uv run python manage.py migrate
fi
