FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpcap-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --upgrade pip \
    && python -m pip wheel --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping libpcap0.8 speedtest-cli tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

# Mantemos o código no caminho histórico porque os volumes persistentes do Pi
# apontam para src/jarvis/database e src/jarvis/storage.
COPY src ./src

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

CMD ["python", "-m", "jarvis.main"]
