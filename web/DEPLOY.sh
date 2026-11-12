#!/bin/bash
# Deployment script for Christmas Gift Exchange on Archimedes
set -euo pipefail

WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$WORKSPACE_DIR"

echo "=== Deploying Christmas Gift Exchange ==="

# 1. Directory Setup & Permissions
echo "Setting up data directory..."
mkdir -p "$WORKSPACE_DIR/data" "$WORKSPACE_DIR/logs"
chmod 700 "$WORKSPACE_DIR/data"

# 2. Dependency Verification
echo "Verifying Python dependencies..."
python3 -m pip install -q -r "$WORKSPACE_DIR/web/requirements.txt"

# 3. Automated Verification Tests
echo "Running automated test suite..."
pytest -q "$WORKSPACE_DIR/tests/test_web.py"

# 4. Permissions check on DB
DB_PATH="${CHRISTMAS_DB_PATH:-$WORKSPACE_DIR/data/christmas.db}"
if [ -f "$DB_PATH" ]; then
    chmod 600 "$DB_PATH"
fi

# 5. Service Manager Delegation
echo "Deploying via Archimedes Service Manager..."
if [ -x "$WORKSPACE_DIR/web/APP.sh" ]; then
    "$WORKSPACE_DIR/web/APP.sh" restart || "$WORKSPACE_DIR/web/APP.sh" install
fi

echo "=== Deployment completed successfully ==="
echo "Application URL: https://taylorarchibald.com/christmas/"
echo "Local Binding:   http://127.0.0.1:13045/christmas/"
echo "Health Check:    http://127.0.0.1:13045/health"
