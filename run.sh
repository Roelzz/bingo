#!/usr/bin/env bash
# Speerpunt Bingo: ./run.sh [start|stop|restart|status|test]
cd "$(dirname "$0")"
PIDFILE=server.pid
LOG=server.log
PORT="${PORT:-8000}"

start() {
  if status >/dev/null 2>&1; then echo "draait al (pid $(cat $PIDFILE))"; return; fi
  set -a; source .env; set +a
  export DATA_DIR="${DATA_DIR:-./data}"
  setsid nohup uv run uvicorn app.main:app --host 0.0.0.0 --port "$PORT" > "$LOG" 2>&1 < /dev/null &
  sleep 5
  pgrep -f "uvicorn app.main:app --host 0.0.0.0 --port $PORT" | tail -1 > "$PIDFILE"
  echo "gestart op 0.0.0.0:$PORT (pid $(cat $PIDFILE)), log: $LOG"
}

status() {
  local pid
  pid=$(pgrep -f "uvicorn app.main:app --host 0.0.0.0 --port $PORT" | tail -1)
  [ -n "$pid" ] || { echo "niet gestart" >&2; return 1; }
  echo "$pid"
}

stop() {
  local pid
  pid=$(pgrep -f "uvicorn app.main:app --host 0.0.0.0 --port $PORT" | tail -1)
  [ -n "$pid" ] || { echo "stopt al niet"; return; }
  kill "$pid" 2>/dev/null; sleep 2
  kill -9 "$pid" 2>/dev/null; rm -f "$PIDFILE"; echo "gestopt"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  test) exec uv run pytest -q ;;
  *) echo "gebruik: ./run.sh [start|stop|restart|status|test]"; exit 1 ;;
esac
