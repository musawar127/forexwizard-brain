#!/usr/bin/env bash
# Start FastAPI backend, fully detached. The bash tool's parent shell ends
# when its command returns, killing un-detached children. This wrapper uses
# setsid + exec + stdin/out/err redirection so the uvicorn process survives.
set -euo pipefail
cd /home/z/my-project/apps/api
export DATABASE_URL=sqlite:///./forexwizard.db
export PYTHONPATH=.
export PYTHONUNBUFFERED=1
LOG=/home/z/my-project/logs/backend.log
PIDF=/home/z/my-project/logs/backend.pid
setsid bash -c '
  exec /home/z/.venv/bin/python -m uvicorn app.main:app \
    --host 0.0.0.0 --port 8000 --log-level info \
    >'"$LOG"' 2>&1 < /dev/null
' &
echo $! > $PIDF
