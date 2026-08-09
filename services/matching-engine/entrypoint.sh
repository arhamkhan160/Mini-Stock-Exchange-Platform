#!/bin/sh
# LF line endings are mandatory (.gitattributes enforces it) or the container
# dies with "exec /app/entrypoint.sh: no such file or directory".
set -e

# No `alembic upgrade head`: the matching engine has no database.
echo "[matching-engine] starting api on ${SERVICE_PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${SERVICE_PORT}"
