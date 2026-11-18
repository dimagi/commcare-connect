#!/bin/bash
# serve.sh [port] — start the commcare-connect dev server with demo settings, in the background.
# Idempotent. Run from the task folder; logs to ./output/server.log. Stop with: serve.sh stop [port]
set -u
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(git -C "$SKILL_DIR" rev-parse --show-toplevel)"
PORT="${1:-8010}"
mkdir -p output

if [ "${1:-}" = "stop" ]; then
  PORT="${2:-8010}"
  pkill -f "runserver.*:$PORT" 2>/dev/null || pkill -f "runserver $PORT" 2>/dev/null
  sleep 1
  curl -s -o /dev/null --max-time 2 "http://localhost:$PORT/" && echo "server still up on $PORT" || echo "server on $PORT stopped"
  exit 0
fi

# Port must be free (a stale server corrupts logs and serves stale code).
if curl -s -o /dev/null --max-time 2 "http://localhost:$PORT/"; then
  echo "port $PORT is in use — pass another port or run: $0 stop $PORT"; exit 1
fi

cd "$REPO" || { echo "repo not found: $REPO"; exit 1; }
# shellcheck disable=SC1091
source .venv/bin/activate || { echo "no .venv in $REPO — run: uv sync"; exit 1; }

# whitenoise ManifestStaticFilesStorage needs the manifest under DEBUG=False; --insecure avoids a collectstatic step.
INSECURE=""
[ -f staticfiles/staticfiles.json ] || INSECURE="--insecure"

PYTHONPATH="$SKILL_DIR" DJANGO_SETTINGS_MODULE=record_settings \
  nohup ./manage.py runserver "$PORT" $INSECURE > "$OLDPWD/output/server.log" 2>&1 &
cd "$OLDPWD"

for _ in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://localhost:$PORT/accounts/login/")
  if [ "$code" = "200" ]; then sleep 2; echo "server ready on http://localhost:$PORT (DEBUG off, email → console)"; exit 0; fi  # settle: a login right at first response once lost its session
  sleep 0.5
done
echo "server did not come up — see output/server.log"; tail -20 output/server.log; exit 1
