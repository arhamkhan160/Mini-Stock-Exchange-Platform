#!/bin/sh
# LF line endings are mandatory (.gitattributes enforces it) or the container
# dies with "exec /app/entrypoint.sh: no such file or directory".
set -e

echo "[user-service] running migrations"
alembic upgrade head

echo "[user-service] starting api on ${SERVICE_PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${SERVICE_PORT}"
