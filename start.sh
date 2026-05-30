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

# Start Celery worker in background with restart loop
echo "Starting Celery worker..."
while true; do
  python -m celery -A tasks worker --pool=solo --loglevel=info 2>&1
  echo "Worker crashed, restarting in 5s..."
  sleep 5
done &

# Give worker time to start
sleep 3

# Start FastAPI in foreground
echo "Starting FastAPI..."
uvicorn main:app --host 0.0.0.0 --port 8000