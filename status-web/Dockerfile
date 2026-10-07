FROM python:3.12-slim-bookworm
WORKDIR /app
RUN pip install --no-cache-dir psycopg[binary] pyyaml
COPY status-web/app.py /app/app.py
EXPOSE 8090
CMD ["python", "/app/app.py"]
