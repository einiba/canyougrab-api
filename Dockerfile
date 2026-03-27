# Multi-purpose image: API server, MCP server, and RQ workers.
# Override CMD per workload in K8s deployment spec.

# ── Stage 1: Build Go bloom-builder binary ──────────────────────────────────
FROM golang:1.22-alpine AS go-builder
WORKDIR /build
COPY go.mod ./
COPY cmd/ ./cmd/
RUN GOFLAGS=-mod=mod CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /bloom-builder ./cmd/bloom-builder/ && \
    GOFLAGS=-mod=mod CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /worker ./cmd/worker/

# ── Stage 2: Python runtime ──────────────────────────────────────────────────
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

# Copy OpenAPI spec (served by the API at /api-reference/openapi.json)
# _resolve_repo_file looks at parent.parent of /app/app.py = /
COPY portal/config/routes.oas.json /portal/config/routes.oas.json

# Copy compiled Go binaries
COPY --from=go-builder /bloom-builder /app/bloom-builder
COPY --from=go-builder /worker /app/worker

EXPOSE 8000 8001

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s \
    CMD curl -sf http://localhost:8000/health || exit 1

# Default: API server
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "3"]
