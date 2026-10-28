FROM python:3.12-alpine3.21

# Commit, aus dem gebaut wird – die Oberfläche zeigt ihn im Footer an:
# docker build --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) ...
ARG GIT_COMMIT=""

LABEL org.opencontainers.image.title="docker-update-ui" \
      org.opencontainers.image.description="Weboberfläche für Docker-Updates mit Daten-Backup, Rollback-Images und Rollback-Skripten" \
      org.opencontainers.image.source="local"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONFIG_DIR=/config \
    BACKUP_DIR=/docker/backups \
    BROWSE_DIR=/docker \
    TZ=Europe/Berlin \
    GIT_COMMIT=${GIT_COMMIT}

WORKDIR /app

RUN apk add --no-cache tzdata \
    && mkdir -p /config /docker/backups

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8080

# Busybox-wget statt eines neuen Python-Prozesses: kostet wenige Millisekunden
# statt rund 60 ms CPU pro Prüfung.
HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=3 \
    CMD ["wget", "-q", "-O", "-", "http://127.0.0.1:8080/healthz"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
