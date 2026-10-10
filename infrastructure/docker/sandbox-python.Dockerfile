# Test-execution sandbox image. Containers run with --network none, so test
# dependencies must be baked in here. Build: make sandbox-image
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest==8.4.2 && useradd --uid 65534 --no-create-home sandbox 2>/dev/null || true
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /workspace
