#!/usr/bin/env bash
# Set up a git worktree: copy .env from the main checkout, install dependencies, and give the
# worktree its own database and Redis database. worktrunk runs this from .config/wt.toml; with
# another worktree tool, run it yourself in the new worktree.

set -euo pipefail

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
uv run python scripts/worktree.py setup
uv run python manage.py migrate
