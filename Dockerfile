# Single-stage build
# Version from git tag, passed via: --build-arg VERSION=$(git describe --tags --always)
ARG VERSION=0.0.0-dev

FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies
# - curl: Health check
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
# --root-user-action=ignore: Suppress warning about running as root (expected in Docker)
RUN pip install --no-cache-dir --upgrade pip --root-user-action=ignore && \
    pip install --no-cache-dir -r requirements.txt --root-user-action=ignore

# Copy application code
COPY azurerbac/ ./azurerbac/

# Write version from build arg
ARG VERSION
RUN echo '"""Auto-generated version file. DO NOT EDIT."""' > ./azurerbac/_version.py && \
    echo "__version__ = \"${VERSION}\"" >> ./azurerbac/_version.py && \
    echo "Version: ${VERSION}"

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    TOKENIZERS_PARALLELISM=false

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Start command — background worker + web server co-located in ONE container.
#
# ARCHITECTURAL NOTE: In a clean production deployment the background worker
# and the web server SHOULD run as separate containers (Container Apps,
# Kubernetes Deployments, or two App Services). Separation gives each tier
# its own lifecycle, log stream, resource limits, scaling rules, and isolates
# a worker crash from the web tier. They are deliberately co-located here to
# save the cost of a second Azure App Service plan in the current deployment.
CMD ["sh", "-c", "python -m azurerbac.backgroundjobs.worker & exec python -m uvicorn azurerbac.web.app:app --host 0.0.0.0 --port ${PORT} --limit-concurrency 256 --h11-max-incomplete-event-size 16384"]
