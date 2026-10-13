FROM cgr.dev/chainguard/python:latest-dev AS build

WORKDIR /app
RUN python -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"
RUN pip install --no-cache-dir psycopg[binary] pyyaml

FROM cgr.dev/chainguard/python:latest

WORKDIR /app
COPY --from=build /app/venv /app/venv
ENV PATH="/app/venv/bin:$PATH"
COPY status-web/app.py /app/app.py
EXPOSE 8090
ENTRYPOINT ["python", "/app/app.py"]
