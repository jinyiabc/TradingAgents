FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY . .
# Install both base and [server] extras so the same builder image feeds
# either runtime target. FastAPI + uvicorn are small; CLI image absorbs
# the cost in exchange for one shared install step.
RUN pip install --no-cache-dir '.[server]'

# ----- shared runtime base -----
FROM python:3.12-slim AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home appuser
USER appuser
WORKDIR /home/appuser/app

COPY --from=builder --chown=appuser:appuser /build .

# ----- web server image -----
# Build with: docker build --target server -t tradingagents-server .
FROM runtime-base AS server
EXPOSE 8000
ENTRYPOINT ["tradingagents-server"]
CMD ["--host", "0.0.0.0", "--port", "8000"]

# ----- CLI image (default target — preserves prior behavior) -----
FROM runtime-base AS cli
ENTRYPOINT ["tradingagents"]
