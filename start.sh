#!/bin/sh
set -e

if [ "$SERVICE_ROLE" = "refresh" ]; then
  echo "Starting refresh job (SERVICE_ROLE=refresh)..."
  exec python refresh_orders.py
fi

echo "Starting Streamlit web app..."
exec streamlit run rewards_simulator.py \
  --server.port "$PORT" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection false
