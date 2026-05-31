#!/bin/bash

echo "=== Starting Holo Backend ==="

# Test Redis connection first
echo "Testing Redis connection..."
python -c "
import os, redis, ssl
url = os.getenv('REDIS_URL')
if url:
    r = redis.from_url(url, ssl_cert_reqs=ssl.CERT_NONE)
    r.ping()
    print('Redis OK')
else:
    print('REDIS_URL not set!')
" 2>&1 || echo "Redis connection failed"

# Only start Celery if REDIS_URL is set
if [ -n "$REDIS_URL" ]; then
  echo "Starting Celery worker..."
  python -m celery -A tasks worker --pool=solo --loglevel=info &
  sleep 3
else
  echo "WARNING: Skipping Celery worker - REDIS_URL not set"
fi

# Start FastAPI in foreground
echo "Starting FastAPI..."
uvicorn main:app --host 0.0.0.0 --port 8000