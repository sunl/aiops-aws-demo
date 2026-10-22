#!/usr/bin/env bash
# Run the Feishu long-connection client (Q&A bot) on this machine.
#   AWS_PROFILE=<demo account profile> scripts/feishu-ws.sh start
#   scripts/feishu-ws.sh status | logs [lines] | stop
# Optional env: ALLOWED_CHAT_IDS=oc_xxx,  FEISHU_BASE_URL=https://open.larksuite.com,  AWS_REGION
# The process is detached (setsid + nohup) so it keeps running after this shell exits.
set -euo pipefail
umask 077  # the log records the connection URL, which carries a ticket
DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$DIR/.local"
PID_FILE="$STATE/feishu-ws.pid"
LOG_FILE="$STATE/feishu-ws.log"
VENV="$DIR/.venv-feishu-ws"
mkdir -p "$STATE"
chmod 700 "$STATE"

running() { [[ -s "$PID_FILE" ]] && kill -0 "$(<"$PID_FILE")" 2>/dev/null; }

case "${1:-status}" in
  start)
    if running; then echo "already running (pid $(<"$PID_FILE"))"; exit 0; fi
    : "${AWS_PROFILE:?set AWS_PROFILE to the demo account profile}"
    if [[ ! -x "$VENV/bin/python" ]]; then
      echo "==> creating $VENV"
      python3.12 -m venv "$VENV"
      "$VENV/bin/pip" install --quiet --no-cache-dir -r "$DIR/src/feishu_ws/requirements.txt"
    fi
    # start each run with a fresh log (keep one previous) so the "connected" check below
    # cannot match a line from an earlier run
    if [[ -f "$LOG_FILE" ]]; then mv -f "$LOG_FILE" "$LOG_FILE.1"; fi
    AWS_REGION="${AWS_REGION:-us-east-1}" nohup setsid "$VENV/bin/python" "$DIR/src/feishu_ws/client.py" \
      >>"$LOG_FILE" 2>&1 </dev/null &
    echo $! >"$PID_FILE"
    # lark-oapi imports slowly on a cold start; wait for "connected" or an early exit
    for _ in $(seq 1 90); do
      if ! running; then echo "client exited, last log lines:"; tail -n 20 "$LOG_FILE"; exit 1; fi
      if tail -n 20 "$LOG_FILE" | grep -q "connected to wss"; then
        echo "connected (pid $(<"$PID_FILE")), log: $LOG_FILE"; exit 0
      fi
      sleep 2
    done
    echo "still starting (pid $(<"$PID_FILE")); check: $0 logs"
    ;;
  stop)
    if running; then kill "$(<"$PID_FILE")"; echo "stopped"; else echo "not running"; fi
    rm -f "$PID_FILE"
    ;;
  status)
    if running; then echo "running (pid $(<"$PID_FILE"))"; else echo "not running"; fi
    ;;
  logs)
    tail -n "${2:-50}" "$LOG_FILE"
    ;;
  *)
    echo "usage: $0 start|stop|status|logs [lines]" >&2
    exit 2
    ;;
esac
