# HitSave Ledger (staff dashboard). Published as ghcr.io/hitsave/ledger by .github/workflows/ledger-image.yml.
# Self-contained: the helper scripts and the public config are baked in; only private config and
# secrets are mounted at runtime (ledger.yaml, database.yaml, omeka-catalog.yaml, secrets/).
FROM cgr.dev/chainguard/python:latest-dev AS build

WORKDIR /app
RUN python -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"
RUN pip install --no-cache-dir psycopg[binary] pyyaml requests boto3

FROM cgr.dev/chainguard/python:latest

LABEL org.opencontainers.image.source="https://github.com/hitsave/hitsave-archiver"
LABEL org.opencontainers.image.description="HitSave Ledger: preservation ledger, health, catalog corrections"
LABEL org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app
COPY --from=build /app/venv /app/venv
ENV PATH="/app/venv/bin:$PATH" \
    HITSAVE_CONFIG_ROOT=/config \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY ledger/*.py ledger/preserve_theme.css /app/
COPY scripts/*.py /app/scripts/
COPY scripts/config_require.py /app/config_require.py
COPY config/ /config/
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["python", "/app/app.py"]
