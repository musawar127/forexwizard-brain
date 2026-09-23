#!/usr/bin/env bash
# Start Next.js dev server, fully detached using setsid+exec.
set -euo pipefail
cd /home/z/my-project/apps/web
LOG=/home/z/my-project/logs/frontend.log
PIDF=/home/z/my-project/logs/frontend.pid
setsid bash -c '
  exec npm run dev -- --hostname 0.0.0.0 --port 3000 \
    >'"$LOG"' 2>&1 < /dev/null
' &
echo $! > $PIDF
