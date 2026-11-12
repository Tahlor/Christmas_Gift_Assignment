#!/bin/bash
# Archimedes Service Manager Wrapper for Christmas Gift Exchange
set -euo pipefail

# Application identity
export APP_NAME="christmas"
export WORKING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export SERVICE_DESCRIPTION="Christmas Gift Exchange Web App"
export DEPENDENCIES="network.target"

# Default environment configuration if not already set in system environment
ENV_FILE="${CHRISTMAS_ENV_FILE:-/home/ubuntu/webapps_data/christmas_exchange/.env}"
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

# Ensure default database path if unset
export CHRISTMAS_DB_PATH="${CHRISTMAS_DB_PATH:-/home/ubuntu/webapps_data/christmas_exchange/christmas.db}"

# Port & execution command
PORT="${CHRISTMAS_PORT:-13046}"
PYTHON_BIN="${PYTHON_BIN:-/home/ubuntu/py311/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then PYTHON_BIN="$(command -v python3)"; fi
export EXEC_CMD="$PYTHON_BIN -m web.app --host 127.0.0.1 --port $PORT"
export WEBAPP_LOG_DIR="/home/ubuntu/webapps_data/christmas_exchange/logs"
mkdir -p "$WEBAPP_LOG_DIR"

# Pre-execution hook called by service_manager in executor mode
pre_exec_hook() {
    # Ensure database parent directory permissions
    mkdir -p "$(dirname "$CHRISTMAS_DB_PATH")"
    chmod 700 "$(dirname "$CHRISTMAS_DB_PATH")" 2>/dev/null || true
    if [ -f "$CHRISTMAS_DB_PATH" ]; then
        chmod 600 "$CHRISTMAS_DB_PATH" 2>/dev/null || true
    fi
}

# Delegate to Archimedes universal service manager
SERVICE_MANAGER="/home/ubuntu/Projects/utils/service_manager.sh"
if [ -f "$SERVICE_MANAGER" ]; then
    source "$SERVICE_MANAGER" "$@"
else
    echo "Service manager script not found at $SERVICE_MANAGER. Executing command directly:"
    pre_exec_hook
    exec $EXEC_CMD "$@"
fi
