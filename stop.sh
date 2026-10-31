#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"
if [[ ! -d "$RUN_DIR" ]]; then echo "ConnectX is not running."; exit 0; fi
target="${1:-all}"
case "$target" in
  all|frontend|backend|reminders|livekit) ;;
  *) echo "Usage: ./stop.sh [all|frontend|backend|reminders|livekit]" >&2; exit 2 ;;
esac
command -v flock >/dev/null || { echo "flock is required to safely stop ConnectX." >&2; exit 1; }
if [[ "$target" == all ]]; then
  exec 9>"$RUN_DIR/start.lock"
  flock 9
fi

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

find_managed_process() {
  local name="$1" pid
  while read -r pid; do
    if is_managed_pid "$name" "$pid"; then printf '%s' "$pid"; return 0; fi
  done < <(ps -eo pid=)
  return 1
}

if [[ "$target" == all ]]; then
  services=(frontend reminders backend)
elif [[ "$target" == livekit ]]; then
  services=()
else
  services=("$target")
fi

for name in "${services[@]}"; do
  pid_file="$RUN_DIR/$name.pid"
  port_file="$RUN_DIR/$name.port"
  pid=""
  [[ -f "$pid_file" ]] && pid="$(cat "$pid_file")"
  if is_managed_pid "$name" "$pid"; then
    :
  else
    if [[ -n "$pid" ]]; then echo "Ignoring stale $name PID marker; no unrelated process was stopped."; fi
    rm -f "$pid_file"
    pid="$(find_managed_process "$name" || true)"
  fi
  if [[ -z "$pid" ]]; then
    rm -f "$port_file"
    continue
  fi
  if is_managed_pid "$name" "$pid"; then
    pgid="$(ps -p "$pid" -o pgid= 2>/dev/null | tr -d ' ' || true)"
    if [[ "$pgid" == "$pid" ]]; then
      kill -TERM -- "-$pid" 2>/dev/null || true
    else
      kill -TERM "$pid" 2>/dev/null || true
    fi
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    if kill -0 "$pid" 2>/dev/null; then
      if [[ "$pgid" == "$pid" ]]; then
        kill -KILL -- "-$pid" 2>/dev/null || true
      else
        kill -KILL "$pid" 2>/dev/null || true
      fi
    fi
    echo "Stopped $name ($pid)."
  fi
  rm -f "$pid_file" "$port_file"
done

if [[ "$target" == all || "$target" == livekit ]] && command -v docker >/dev/null && docker compose ps --status running -q livekit 2>/dev/null | grep -q .; then
  docker compose stop livekit >/dev/null
  echo "Stopped LiveKit."
fi
