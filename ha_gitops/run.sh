#!/usr/bin/with-contenv bashio
# ==============================================================================
# HA-GitOps add-on entrypoint
# ==============================================================================
set -e

LOG_LEVEL="$(bashio::config 'log_level')"
export LOG_LEVEL

bashio::log.info "Starting HA-GitOps engine (log_level=${LOG_LEVEL})..."

# /data persists across restarts/updates and holds the clone, state DB and token.
mkdir -p /data

cd /app
exec python3 -m uvicorn engine.app:app \
    --host 0.0.0.0 \
    --port 8099 \
    --no-access-log \
    --proxy-headers
