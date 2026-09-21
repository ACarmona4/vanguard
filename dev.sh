#!/usr/bin/env bash

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"
API_PID_FILE="$RUN_DIR/api.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
API_PORT="${API_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

is_running() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && lsof -p "$pid" >/dev/null 2>&1
}

process_command() {
  ps -p "$1" -o command= 2>/dev/null || true
}

process_cwd() {
  lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1
}

matches_project_process() {
  local pid="$1"
  local kind="$2"
  local command cwd
  command="$(process_command "$pid")"
  cwd="$(process_cwd "$pid")"

  if [[ "$kind" == "api" ]]; then
    [[ "$command" == *"vanguard_inventory.api:app"* || "$cwd" == "$ROOT_DIR" || "$cwd" == "$ROOT_DIR/backend"* ]]
  else
    [[ ("$command" == *"$ROOT_DIR/frontend"* && "$command" == *"vite"*) || "$cwd" == "$ROOT_DIR/frontend"* ]]
  fi
}

project_root_pid() {
  local pid="$1"
  local kind="$2"
  local parent

  while true; do
    parent="$(ps -p "$pid" -o ppid= 2>/dev/null | tr -d ' ' || true)"
    if [[ -z "$parent" || "$parent" -le 1 ]] || ! matches_project_process "$parent" "$kind"; then
      break
    fi
    pid="$parent"
  done
  printf '%s\n' "$pid"
}

pid_on_port() {
  lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -n 1
}

managed_pid() {
  local pid_file="$1"
  local port="$2"
  local kind="$3"
  local pid=""

  if [[ -f "$pid_file" ]]; then
    pid="$(<"$pid_file")"
    if is_running "$pid" && matches_project_process "$pid" "$kind"; then
      printf '%s\n' "$pid"
      return
    fi
    rm -f "$pid_file"
  fi

  pid="$(pid_on_port "$port")"
  if is_running "$pid" && matches_project_process "$pid" "$kind"; then
    project_root_pid "$pid" "$kind"
  fi
}

terminate_tree() {
  local pid="$1"
  local signal="${2:-TERM}"
  local children child
  children="$(pgrep -P "$pid" 2>/dev/null || true)"

  kill -"$signal" "$pid" 2>/dev/null || true
  for child in $children; do
    terminate_tree "$child" "$signal"
  done
}

stop_service() {
  local name="$1"
  local pid_file="$2"
  local port="$3"
  local kind="$4"
  local pid
  pid="$(managed_pid "$pid_file" "$port" "$kind")"

  if [[ -z "$pid" ]]; then
    local occupied
    occupied="$(pid_on_port "$port")"
    if [[ -n "$occupied" ]]; then
      echo "$name: el puerto $port pertenece a otro proceso; no se modificó."
    else
      echo "$name: detenido."
    fi
    rm -f "$pid_file"
    return
  fi

  echo "Deteniendo $name (PID $pid)…"
  terminate_tree "$pid"
  local attempt remaining
  for attempt in {1..20}; do
    [[ -z "$(pid_on_port "$port")" ]] && break
    sleep 0.1
  done
  remaining="$(pid_on_port "$port")"
  if [[ -n "$remaining" ]] && matches_project_process "$remaining" "$kind"; then
    terminate_tree "$(project_root_pid "$remaining" "$kind")" "KILL"
  fi
  rm -f "$pid_file"
}

stop_all() {
  stop_service "API" "$API_PID_FILE" "$API_PORT" "api"
  stop_service "frontend" "$FRONTEND_PID_FILE" "$FRONTEND_PORT" "frontend"
  rmdir "$RUN_DIR" 2>/dev/null || true
}

port_is_available() {
  local name="$1"
  local port="$2"
  local pid
  pid="$(pid_on_port "$port")"
  if [[ -n "$pid" ]]; then
    echo "No se puede iniciar $name: el puerto $port está ocupado por el PID $pid." >&2
    echo "Ejecuta './dev.sh stop' si corresponde a Vanguard." >&2
    return 1
  fi
}

start_all() {
  if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
    echo "No existe .venv/bin/python. Crea el entorno e instala requirements.txt." >&2
    exit 1
  fi
  if [[ ! -x "$ROOT_DIR/frontend/node_modules/.bin/vite" ]]; then
    echo "Faltan dependencias del frontend. Ejecuta 'npm --prefix frontend install'." >&2
    exit 1
  fi

  port_is_available "la API" "$API_PORT" || exit 1
  port_is_available "el frontend" "$FRONTEND_PORT" || exit 1
  local metrics_enabled
  metrics_enabled="$(cd "$ROOT_DIR" && "$ROOT_DIR/.venv/bin/python" -c 'import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv("VANGUARD_METRICS_ENABLED", "true").lower())')"
  if [[ "$metrics_enabled" != "false" && "$metrics_enabled" != "0" && "$metrics_enabled" != "off" && "$metrics_enabled" != "no" ]]; then
    if ! (cd "$ROOT_DIR" && docker compose up -d otel-collector prometheus); then
      echo "Observabilidad no disponible. Inicia Docker y ejecuta 'make observability'." >&2
    fi
  fi
  mkdir -p "$RUN_DIR"

  (
    cd "$ROOT_DIR" || exit 1
    exec env PYTHONPATH="$ROOT_DIR/backend/src" \
      "$ROOT_DIR/.venv/bin/python" -m uvicorn vanguard_inventory.api:app \
      --app-dir "$ROOT_DIR/backend/src" --reload --host 127.0.0.1 --port "$API_PORT"
  ) &
  local api_pid=$!
  printf '%s\n' "$api_pid" > "$API_PID_FILE"

  (
    cd "$ROOT_DIR/frontend" || exit 1
    exec "$ROOT_DIR/frontend/node_modules/.bin/vite" \
      --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort
  ) &
  local frontend_pid=$!
  printf '%s\n' "$frontend_pid" > "$FRONTEND_PID_FILE"

  trap 'exit 130' INT TERM
  trap 'stop_all' EXIT

  echo "API:      http://127.0.0.1:$API_PORT"
  echo "Frontend: http://127.0.0.1:$FRONTEND_PORT"
  echo "Presiona Ctrl+C para detener ambos procesos."

  while is_running "$api_pid" && is_running "$frontend_pid"; do
    sleep 1
  done

  echo "Uno de los procesos terminó; cerrando el entorno completo."
  trap - EXIT INT TERM
  stop_all
  wait "$api_pid" "$frontend_pid" 2>/dev/null || true
}

show_status() {
  local api_pid frontend_pid
  api_pid="$(managed_pid "$API_PID_FILE" "$API_PORT" "api")"
  frontend_pid="$(managed_pid "$FRONTEND_PID_FILE" "$FRONTEND_PORT" "frontend")"

  [[ -n "$api_pid" ]] && echo "API: activa (PID $api_pid, puerto $API_PORT)" || echo "API: detenida"
  [[ -n "$frontend_pid" ]] && echo "Frontend: activo (PID $frontend_pid, puerto $FRONTEND_PORT)" || echo "Frontend: detenido"
}

case "${1:-start}" in
  start) start_all ;;
  stop) stop_all ;;
  status) show_status ;;
  *)
    echo "Uso: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
