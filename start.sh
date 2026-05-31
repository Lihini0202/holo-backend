#!/bin/bash

echo "=== Starting Holo Backend ==="

# Start FastAPI FIRST so health check passes quickly
echo "Starting FastAPI..."
uvicorn main:app --host 0.0.0.0 --port 8000 &
FASTAPI_PID=$!

# Wait for FastAPI to be ready
sleep 2

# Only start Celery if REDIS_URL is set
if [ -n "$REDIS_URL" ]; then
  echo "Starting Celery worker..."
  python -m celery -A tasks worker --pool=solo --loglevel=info &
else
  echo "WARNING: Skipping Celery worker - REDIS_URL not set"
fi

# Keep container alive by waiting on FastAPI
wait $FASTAPI_PID