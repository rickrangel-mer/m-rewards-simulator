#!/bin/sh
set -e

if [ "$SERVICE_ROLE" = "refresh" ]; then
  echo "Starting refresh job (SERVICE_ROLE=refresh)..."
  exec python refresh_orders.py
fi

PORT="${PORT:-8080}"
echo "Starting FastAPI web app on port ${PORT}..."
exec python -m uvicorn app:app --host 0.0.0.0 --port "$PORT"
