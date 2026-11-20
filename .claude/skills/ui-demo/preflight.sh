#!/bin/bash
# preflight.sh — check this machine can run ui-demo before anything is installed or started.
# Prints one line per problem; exits 1 if a hard requirement is missing. Warnings (!) don't fail.
set -u
MIN_NODE=20  # playwright's package.json engines
fail=0

missing() { echo "✗ $1"; fail=1; }
warn() { echo "! $1"; }
has() { command -v "$1" >/dev/null 2>&1; }

if has node; then
  major=$(node -p 'process.versions.node.split(".")[0]')
  [ "$major" -ge "$MIN_NODE" ] || missing "Node $major is too old — Playwright needs $MIN_NODE+"
else
  missing "node not found — install Node $MIN_NODE+ (brew install node, or nvm)"
fi
has npm || missing "npm not found — it ships with Node"

if has python3; then
  python3 -c 'import venv, ensurepip' 2>/dev/null || missing "python3 can't create a venv — install python3-venv (Linux) or a full Python 3"
else
  missing "python3 not found — needed for render.py (Pillow)"
fi

if has ffmpeg && has ffprobe; then
  ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264 || missing "ffmpeg has no libx264 encoder — install a full build (brew install ffmpeg / apt install ffmpeg)"
else
  missing "ffmpeg/ffprobe not found — brew install ffmpeg (Linux: apt install ffmpeg)"
fi

has curl || missing "curl not found"

if [ "$(uname)" = "Linux" ] && [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  warn "no display — the Record Pass runs a headed browser; use a desktop session or wrap it in xvfb-run"
fi
# Chromium's system libraries can't be installed without sudo; two that fresh installs often lack stand in for the rest.
if [ "$(uname)" = "Linux" ] && ! { ldconfig -p 2>/dev/null | grep -q libnss3 && ldconfig -p 2>/dev/null | grep -q libgbm; }; then
  warn "Chromium's system libraries look missing — run once: sudo npx playwright install-deps chromium"
fi

REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
[ -x "$REPO/.venv/bin/python" ] || missing "no $REPO/.venv — serve.sh and reseed.sh run Django from it (run: uv sync)"

if [ "${1:-}" = "--voiceover" ]; then
  has uv || missing "uv not found — narrate.py runs Chatterbox through it (brew install uv)"
  # hw.optional.arm64 is 1 on Apple Silicon even under Rosetta, where uname -m says x86_64
  if [ "$(uname)" = "Darwin" ] && [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" != 1 ]; then
    missing "the voiceover can't run on an Intel Mac — Chatterbox needs torch 2.6, which has no Intel Mac build"
  fi
fi

[ "$fail" = 0 ] && echo "✓ preflight ok"
exit "$fail"
