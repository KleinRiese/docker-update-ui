FROM python:3.12-alpine

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
    TZ=Europe/Berlin

WORKDIR /app

RUN apk add --no-cache tzdata \
    && mkdir -p /config /docker/backups

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
