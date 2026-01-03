# Single-stage build - ColBERT requires g++ at runtime anyway
# Version from git tag, passed via: --build-arg VERSION=$(git describe --tags --always)
ARG VERSION=0.0.0-dev

FROM python:3.12-slim

WORKDIR /app

# Install dependencies (build tools needed for ColBERT's runtime compilation)
# - git: Required by GitPython (RAGatouille/ColBERT dependency)
# - build-essential, g++: Required to compile segmented_maxsim_cpp extension
# - ninja-build: Fast build system used by PyTorch extensions
# - curl: Health check
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    g++ \
    ninja-build \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
# --root-user-action=ignore: Suppress warning about running as root (expected in Docker)
RUN pip install --no-cache-dir --upgrade pip --root-user-action=ignore && \
    pip install --no-cache-dir -r requirements.txt --root-user-action=ignore

# Pre-compile ColBERT's C++ extension during build
RUN python -c "import os; os.environ['GIT_PYTHON_REFRESH']='quiet'; from ragatouille import RAGPretrainedModel; print('ColBERT extension compiled successfully')" || echo "ColBERT extension will be compiled at runtime"

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
    GIT_PYTHON_REFRESH=quiet \
    TOKENIZERS_PARALLELISM=false

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Start command - background worker + web server
CMD ["sh", "-c", "python -m azurerbac.backgroundjobs.worker & python -m uvicorn azurerbac.web.app:app --host 0.0.0.0 --port ${PORT}"]
