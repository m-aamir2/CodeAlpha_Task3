#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
RUN_DIR="$ROOT_DIR/.run"
mkdir -p "$RUN_DIR"

command -v flock >/dev/null || { echo "flock is required to prevent duplicate startup." >&2; exit 1; }
exec 9>"$RUN_DIR/start.lock"
if ! flock -n 9; then
  echo "ConnectX startup is already in progress; leaving existing services alone."
  exit 0
fi

command -v node >/dev/null || { echo "Node.js 20+ is required." >&2; exit 1; }
command -v npm >/dev/null || { echo "npm is required." >&2; exit 1; }
NODE_VERSION="$(node -p 'process.versions.node.split(".").slice(0,2).join(".")')"
if [[ "$(printf '%s\n' 20 "$NODE_VERSION" | sort -V | head -n1)" != "20" ]]; then
  echo "Node.js 20 or newer is required; found $NODE_VERSION." >&2; exit 1
fi
if command -v python3.12 >/dev/null; then PYTHON_BIN="$(command -v python3.12)"; else PYTHON_BIN="$(command -v python3 || true)"; fi
if [[ -z "$PYTHON_BIN" ]]; then echo "Python 3.12+ is required." >&2; exit 1; fi
PYTHON_VERSION="$($PYTHON_BIN -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [[ "$(printf '%s\n' 3.12 "$PYTHON_VERSION" | sort -V | head -n1)" != "3.12" ]]; then
  echo "Python 3.12 or newer is required; found $PYTHON_VERSION." >&2; exit 1
fi
command -v curl >/dev/null || { echo "curl is required for local startup checks." >&2; exit 1; }

REQUESTED_BACKEND_PORT="${BACKEND_PORT:-8000}"
REQUESTED_FRONTEND_PORT="${FRONTEND_PORT:-5173}"

if [[ ! -d backend/.venv ]]; then "$PYTHON_BIN" -m venv backend/.venv; fi
VENV_PYTHON="$ROOT_DIR/backend/.venv/bin/python"
if [[ ! -f .env ]]; then
  cp .env.example .env
  "$PYTHON_BIN" - <<'PY'
from pathlib import Path
import secrets
path = Path('.env')
text = path.read_text()
text = text.replace('replace-this-with-a-long-random-secret', secrets.token_urlsafe(48))
path.write_text(text)
PY
  echo "Created .env with a generated local Django secret. Add LiveKit/email settings when ready."
fi
if [[ ! -f frontend/.env ]]; then cp frontend/.env.example frontend/.env; fi

set -a
# Values in .env should use standard dotenv syntax; URL-encode reserved characters in service URLs.
source .env
set +a

process_args() { ps -p "$1" -o args= 2>/dev/null || true; }
process_cwd() { readlink -f "/proc/$1/cwd" 2>/dev/null || true; }

is_managed_pid() {
  local name="$1" pid="$2" args cwd
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null || return 1
  args="$(process_args "$pid")"
  cwd="$(process_cwd "$pid")"
  case "$name" in
    backend)
      [[ "$args" == *"$ROOT_DIR/backend/.venv/bin/daphne"* && "$args" == *"connectx.asgi:application"* ]]
      ;;
    frontend)
      [[ "$cwd" == "$ROOT_DIR" && "$args" == *"npm run dev --host 127.0.0.1 --strictPort --port"* ]] ||
        [[ "$args" == *"$ROOT_DIR/frontend/node_modules/.bin/vite"* ]]
      ;;
    reminders)
      [[ "$args" == *"$ROOT_DIR/backend/.venv/bin/python"* && "$args" == *"$ROOT_DIR/backend/manage.py run_notifications"* ]]
      ;;
    *) return 1 ;;
  esac
}

read_port_file() {
  local file="$1" port=""
  [[ -f "$file" ]] && port="$(cat "$file")"
  if [[ "$port" =~ ^[0-9]+$ ]] && (( port > 0 && port < 65536 )); then printf '%s' "$port"; fi
  return 0
}

port_from_process() {
  local name="$1" pid="$2" args
  args="$(process_args "$pid")"
  if [[ "$name" == backend && "$args" =~ [[:space:]]-p[[:space:]]+([0-9]+) ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  elif [[ "$name" == frontend && "$args" =~ --port[[:space:]]+([0-9]+) ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  fi
  return 0
}

find_managed_process() {
  local name="$1" pid
  while read -r pid; do
    if is_managed_pid "$name" "$pid"; then printf '%s' "$pid"; return 0; fi
  done < <(ps -eo pid=)
  return 1
}

get_service_pid() {
  local name="$1" pid_file pid
  pid_file="$RUN_DIR/$name.pid"
  if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file")"
    if is_managed_pid "$name" "$pid"; then printf '%s' "$pid"; return 0; fi
    echo "Ignoring stale $name PID marker; no process was stopped." >&2
    rm -f "$pid_file"
  fi
  find_managed_process "$name" || true
}

backend_healthy() {
  local port="$1" body
  body="$(curl --silent --show-error --fail --connect-timeout 1 --max-time 2 "http://127.0.0.1:$port/api/health/" 2>/dev/null)" || return 1
  [[ "$body" == *'"status":"ok"'* || "$body" == *'"status": "ok"'* ]]
}

frontend_healthy() {
  local port="$1" body
  body="$(curl --silent --fail --connect-timeout 1 --max-time 2 "http://127.0.0.1:$port/" 2>/dev/null)" || return 1
  [[ "$body" == *'id="root"'* && "$body" == *'src/main.tsx'* ]]
}

port_is_available() {
  "$PYTHON_BIN" - "$1" <<'PY'
import socket
import sys
port = int(sys.argv[1])
with socket.socket() as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit(1)
PY
}

find_free_port() {
  "$PYTHON_BIN" - "$1" "${2:-}" <<'PY'
import socket
import sys
start = int(sys.argv[1])
excluded = {int(value) for value in sys.argv[2].split(",") if value}
if not 1 <= start <= 65535:
    raise SystemExit(f"Invalid TCP port: {start}.")
for port in range(start, min(start + 100, 65536)):
    if port in excluded:
        continue
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            continue
        print(port)
        break
else:
    raise SystemExit(f"No available TCP port from {start} through {min(start + 99, 65535)}.")
PY
}

BACKEND_PID="$(get_service_pid backend)"
FRONTEND_PID="$(get_service_pid frontend)"
REMINDERS_PID="$(get_service_pid reminders)"
BACKEND_ACTIVE=false
FRONTEND_ACTIVE=false
REMINDERS_ACTIVE=false
if [[ -n "$REMINDERS_PID" ]]; then REMINDERS_ACTIVE=true; fi

BACKEND_PORT=""
if [[ -n "$BACKEND_PID" ]]; then
  BACKEND_PORT="$(port_from_process backend "$BACKEND_PID")"
  [[ -n "$BACKEND_PORT" ]] || BACKEND_PORT="$(read_port_file "$RUN_DIR/backend.port")"
  [[ -n "$BACKEND_PORT" ]] || BACKEND_PORT="$REQUESTED_BACKEND_PORT"
  BACKEND_ACTIVE=true
elif [[ -f "$RUN_DIR/backend.port" ]]; then
  BACKEND_PORT="$(read_port_file "$RUN_DIR/backend.port")"
  if [[ -n "$BACKEND_PORT" ]] && backend_healthy "$BACKEND_PORT"; then
    BACKEND_ACTIVE=true
  fi
fi
if [[ -z "$BACKEND_PORT" ]]; then
  BACKEND_PORT="$REQUESTED_BACKEND_PORT"
  if backend_healthy "$BACKEND_PORT"; then
    BACKEND_ACTIVE=true
  elif ! port_is_available "$BACKEND_PORT"; then
    BACKEND_PORT="$(find_free_port "$BACKEND_PORT")"
  fi
fi
if [[ "$BACKEND_ACTIVE" == true ]]; then
  printf '%s\n' "$BACKEND_PORT" >"$RUN_DIR/backend.port"
  [[ -n "$BACKEND_PID" ]] && printf '%s\n' "$BACKEND_PID" >"$RUN_DIR/backend.pid"
else
  echo "Backend is inactive; it will start on port $BACKEND_PORT."
fi

FRONTEND_PORT=""
if [[ -n "$FRONTEND_PID" ]]; then
  FRONTEND_PORT="$(port_from_process frontend "$FRONTEND_PID")"
  [[ -n "$FRONTEND_PORT" ]] || FRONTEND_PORT="$(read_port_file "$RUN_DIR/frontend.port")"
  [[ -n "$FRONTEND_PORT" ]] || FRONTEND_PORT="$REQUESTED_FRONTEND_PORT"
  FRONTEND_ACTIVE=true
elif [[ -f "$RUN_DIR/frontend.port" ]]; then
  FRONTEND_PORT="$(read_port_file "$RUN_DIR/frontend.port")"
  if [[ -n "$FRONTEND_PORT" ]] && frontend_healthy "$FRONTEND_PORT"; then
    FRONTEND_ACTIVE=true
  fi
fi
if [[ -z "$FRONTEND_PORT" ]]; then
  FRONTEND_PORT="$REQUESTED_FRONTEND_PORT"
  if frontend_healthy "$FRONTEND_PORT"; then
    FRONTEND_ACTIVE=true
  elif ! port_is_available "$FRONTEND_PORT"; then
    FRONTEND_PORT="$(find_free_port "$FRONTEND_PORT" "$BACKEND_PORT")"
  fi
fi
if [[ "$BACKEND_PORT" == "$FRONTEND_PORT" ]]; then
  echo "Frontend and backend ports resolved to the same port ($BACKEND_PORT)." >&2
  exit 1
fi
if [[ "$FRONTEND_ACTIVE" == true ]]; then
  printf '%s\n' "$FRONTEND_PORT" >"$RUN_DIR/frontend.port"
  [[ -n "$FRONTEND_PID" ]] && printf '%s\n' "$FRONTEND_PID" >"$RUN_DIR/frontend.pid"
else
  echo "Frontend is inactive; it will start on port $FRONTEND_PORT."
fi
if [[ "$REMINDERS_ACTIVE" == true ]]; then
  [[ -n "$REMINDERS_PID" ]] && printf '%s\n' "$REMINDERS_PID" >"$RUN_DIR/reminders.pid"
else
  echo "Reminder worker is inactive; it will be started."
fi

export FRONTEND_ORIGIN="http://localhost:$FRONTEND_PORT,http://127.0.0.1:$FRONTEND_PORT"
export VITE_API_URL="http://localhost:$BACKEND_PORT/api"
export VITE_WS_URL="ws://localhost:$BACKEND_PORT"

if [[ "${LIVEKIT_URL:-}" == "ws://localhost:7880" || "${LIVEKIT_URL:-}" == "ws://127.0.0.1:7880" ]]; then
  if [[ "${LIVEKIT_API_KEY:-}" != "devkey" || "${LIVEKIT_API_SECRET:-}" != "secret" ]]; then
    echo "Local LiveKit uses the development key devkey/secret. Set all three LIVEKIT_* values to a Cloud or secured server configuration instead." >&2
    exit 1
  fi
  if ! "$PYTHON_BIN" -c 'import socket; s=socket.socket(); s.settimeout(0.3); result=s.connect_ex(("127.0.0.1",7880)); s.close(); raise SystemExit(result != 0)'; then
    command -v docker >/dev/null || { echo "Docker is required for the bundled local LiveKit server. Install LiveKit separately or configure LiveKit Cloud." >&2; exit 1; }
    running_livekit="$(docker compose ps --status running -q livekit 2>/dev/null || true)"
    if [[ -n "$running_livekit" ]]; then
      echo "LiveKit container is already running; leaving it untouched."
    else
      docker compose up -d livekit
    fi
    livekit_ready=false
    for _ in $(seq 1 30); do
      if "$PYTHON_BIN" -c 'import socket; s=socket.socket(); s.settimeout(0.3); result=s.connect_ex(("127.0.0.1",7880)); s.close(); raise SystemExit(result != 0)'; then livekit_ready=true; break; fi
      running_livekit="$(docker compose ps --status running -q livekit 2>/dev/null || true)"
      if [[ -z "$running_livekit" ]]; then
        echo "LiveKit did not start. Recent logs:" >&2
        docker compose logs --tail 40 livekit >&2
        exit 1
      fi
      sleep 0.5
    done
    if [[ "$livekit_ready" != true ]]; then
      echo "LiveKit did not open port 7880. Recent logs:" >&2
      docker compose logs --tail 40 livekit >&2
      exit 1
    fi
  else
    echo "LiveKit is already accepting connections; leaving it untouched."
  fi
fi

if [[ "$BACKEND_ACTIVE" != true ]]; then
  if [[ ! -d backend/.venv ]]; then "$PYTHON_BIN" -m venv backend/.venv; fi
  requirements_hash="$(sha256sum backend/requirements.txt | awk '{print $1}')"
  installed_hash="$(cat "$RUN_DIR/backend-requirements.sha256" 2>/dev/null || true)"
  if [[ "$requirements_hash" != "$installed_hash" ]]; then
    "$VENV_PYTHON" -m pip install --disable-pip-version-check -r backend/requirements.txt
    printf '%s\n' "$requirements_hash" >"$RUN_DIR/backend-requirements.sha256"
  fi
  "$VENV_PYTHON" backend/manage.py check
  "$VENV_PYTHON" backend/manage.py migrate --noinput
else
  echo "Backend already running on port $BACKEND_PORT; leaving it untouched."
fi

if [[ "$FRONTEND_ACTIVE" == true ]]; then
  echo "Frontend already running on port $FRONTEND_PORT; leaving it untouched."
elif [[ ! -d frontend/node_modules || frontend/package.json -nt frontend/package-lock.json ]]; then
  (cd frontend && npm ci)
fi

launch() {
  local name="$1"; shift
  local logfile="$RUN_DIR/$name.log"
  setsid "$@" 9>&- >>"$logfile" 2>&1 </dev/null &
  local pid=$!
  printf '%s\n' "$pid" >"$RUN_DIR/$name.pid"
}

if [[ "$BACKEND_ACTIVE" != true ]]; then
  printf '%s\n' "$BACKEND_PORT" >"$RUN_DIR/backend.port"
  launch backend bash -c 'cd "$1" && shift && exec "$@"' _ "$ROOT_DIR/backend" "$ROOT_DIR/backend/.venv/bin/daphne" -b 127.0.0.1 -p "$BACKEND_PORT" connectx.asgi:application
fi
if [[ "$REMINDERS_ACTIVE" != true ]]; then
  launch reminders "$VENV_PYTHON" "$ROOT_DIR/backend/manage.py" run_notifications
fi
if [[ "$FRONTEND_ACTIVE" != true ]]; then
  printf '%s\n' "$FRONTEND_PORT" >"$RUN_DIR/frontend.port"
  launch frontend npm --prefix "$ROOT_DIR/frontend" run dev -- --host 127.0.0.1 --strictPort --port "$FRONTEND_PORT"
fi

ready=false
for _ in $(seq 1 40); do
  backend_ready=false
  frontend_ready=false
  backend_healthy "$BACKEND_PORT" && backend_ready=true
  frontend_healthy "$FRONTEND_PORT" && frontend_ready=true
  reminders_ready=false
  reminders_pid_now="$(get_service_pid reminders)"
  if [[ -n "$reminders_pid_now" ]]; then
    REMINDERS_PID="$reminders_pid_now"
    REMINDERS_ACTIVE=true
    printf '%s\n' "$REMINDERS_PID" >"$RUN_DIR/reminders.pid"
    reminders_ready=true
  fi
  if [[ "$backend_ready" == true && "$frontend_ready" == true && "$reminders_ready" == true ]]; then ready=true; break; fi
  sleep 0.5
done
if [[ "$ready" != true ]]; then
  echo "ConnectX services did not all become ready. No running service was stopped." >&2
  echo "Backend health: $(backend_healthy "$BACKEND_PORT" && echo ok || echo unavailable) on $BACKEND_PORT" >&2
  echo "Frontend health: $(frontend_healthy "$FRONTEND_PORT" && echo ok || echo unavailable) on $FRONTEND_PORT" >&2
  for name in backend reminders frontend; do echo "--- $name ---" >&2; tail -n 20 "$RUN_DIR/$name.log" >&2 || true; done
  exit 1
fi

echo "ConnectX is running."
echo "Frontend: http://localhost:$FRONTEND_PORT"
echo "Backend:  http://localhost:$BACKEND_PORT/api"
echo "Health:   http://localhost:$BACKEND_PORT/api/health/"
echo "Logs:     $RUN_DIR/*.log"
echo "Stop services only with: ./stop.sh"
if [[ -z "${LIVEKIT_API_KEY:-}" || -z "${LIVEKIT_API_SECRET:-}" || -z "${LIVEKIT_URL:-}" ]]; then
  echo "Video calls are disabled until LiveKit credentials are set in .env."
fi
