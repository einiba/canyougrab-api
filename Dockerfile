# Multi-purpose image: API server, MCP server, and RQ workers.
# Override CMD per workload in K8s deployment spec.
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps (cached layer)
COPY backend/requirements.txt /app/requirements.txt
COPY mcp-server/ /app/mcp-server/
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir "/app/mcp-server[remote]"

# Copy application code + scripts
COPY backend/ /app/
COPY scripts/ /app/scripts/

EXPOSE 8000 8001

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s \
    CMD curl -sf http://localhost:8000/health || exit 1

# Default: API server
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "3"]
