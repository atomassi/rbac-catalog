# Single-stage build
# Version from git tag, passed via: --build-arg VERSION=$(git describe --tags --always)
ARG VERSION=0.0.0-dev

# Base image pinned by digest for reproducible builds.
FROM python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203

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
COPY rbaccatalog/ ./rbaccatalog/

# Write version from build arg
ARG VERSION
RUN echo '"""Auto-generated version file. DO NOT EDIT."""' > ./rbaccatalog/_version.py && \
    echo "__version__ = \"${VERSION}\"" >> ./rbaccatalog/_version.py && \
    echo "Version: ${VERSION}"

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    TOKENIZERS_PARALLELISM=false

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Web server only; scans run as separate Container Apps Jobs (see
# infra/modules/containerappjobs.bicep).
CMD ["sh", "-c", "exec python -m uvicorn rbaccatalog.web.app:app --host 0.0.0.0 --port ${PORT} --limit-concurrency 256 --h11-max-incomplete-event-size 16384"]
