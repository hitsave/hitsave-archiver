FROM cgr.dev/chainguard/python:latest-dev AS build

WORKDIR /app
RUN python -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"
RUN pip install --no-cache-dir psycopg[binary] pyyaml requests boto3

FROM cgr.dev/chainguard/python:latest

WORKDIR /app
COPY --from=build /app/venv /app/venv
ENV PATH="/app/venv/bin:$PATH"
COPY status-web/app.py /app/app.py
COPY status-web/catalog_correct.py /app/catalog_correct.py
COPY status-web/site_ui.py /app/site_ui.py
COPY status-web/table_sort.py /app/table_sort.py
COPY status-web/pagination.py /app/pagination.py
COPY status-web/preserve_theme.css /app/preserve_theme.css
COPY status-web/ledger_health.py /app/ledger_health.py
COPY status-web/windmill_client.py /app/windmill_client.py
COPY scripts/config_require.py /app/config_require.py
EXPOSE 8090
ENTRYPOINT ["python", "/app/app.py"]
