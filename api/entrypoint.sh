#!/bin/sh
# Container entrypoint for the API service.
#
# Applies any pending database migrations before starting the application so a
# fresh `docker compose up` provisions the schema automatically. The actual
# server command is provided via the image CMD and executed with `exec "$@"`,
# which makes the process PID 1 (proper signal handling / graceful shutdown).
set -e

echo "[entrypoint] Applying database migrations (alembic upgrade head)..."
alembic upgrade head
echo "[entrypoint] Migrations complete. Starting: $*"

exec "$@"
