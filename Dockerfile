# -- Build stage ---------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt


# -- Runtime stage -------------------------------------------------------------
FROM python:3.12-slim

LABEL org.opencontainers.image.title="Hashbot" \
      org.opencontainers.image.description="Automated Bitcoin hashrate arbitrage for Braiins Hashpower Market" \
      org.opencontainers.image.licenses="MIT"

RUN groupadd -r hashbot && useradd -r -g hashbot -d /app hashbot

WORKDIR /app

COPY --from=builder /install /usr/local

# Application code — owned by hashbot so the non-root user can read it
COPY --chown=hashbot:hashbot paths.py config.py api.py main.py dashboard.py keystore.py ./
COPY --chown=hashbot:hashbot templates/ ./templates/

# Data volume — config.json and all runtime state live here
RUN mkdir -p /data && chown hashbot:hashbot /data

USER hashbot

EXPOSE 8000

CMD ["python", "dashboard.py"]
