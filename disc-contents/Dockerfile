# Disc contents extractor (scripts/extract_disc_contents.py): reads a staged data-disc dump and
# writes its files plus web copies (MP4, contact sheets, PDFs, page thumbnails). Runs with no
# network and ready/ mounted read-only (compose service disc-contents, profile tools).
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ffmpeg \
      file \
      fonts-dejavu-core \
      libreoffice-calc-nogui \
      libreoffice-impress-nogui \
      libreoffice-writer-nogui \
      p7zip-full \
      poppler-utils \
      python3 \
      python3-pil \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 --shell /bin/bash contents
WORKDIR /app
COPY scripts/extract_disc_contents.py /app/extract_disc_contents.py
COPY disc-contents/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
# Starts as root only to take ownership of /output (see entrypoint.sh); extraction runs as contents.
ENTRYPOINT ["/app/entrypoint.sh"]
