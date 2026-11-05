# Isolated tooling for CSS-protected DVD-Video menu screenshots (not part of ingest-worker).
# Intake runs it per staged package (docker compose service dvd-menu-preview, no network).
FROM debian:bookworm-slim

ARG LIBDVDCSS_VERSION=1.4.3

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      ffmpeg \
      p7zip-full \
      python3 \
      python3-pil \
      python3-yaml \
      vlc \
      build-essential \
      gcc \
      libtool \
      autoconf \
      automake \
      pkg-config \
    && curl -fsSL -o /tmp/libdvdcss.tar.bz2 \
      "https://download.videolan.org/pub/libdvdcss/${LIBDVDCSS_VERSION}/libdvdcss-${LIBDVDCSS_VERSION}.tar.bz2" \
    && tar xf /tmp/libdvdcss.tar.bz2 -C /tmp \
    && cd "/tmp/libdvdcss-${LIBDVDCSS_VERSION}" \
    && ./configure --prefix=/usr \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig \
    && rm -rf "/tmp/libdvdcss-${LIBDVDCSS_VERSION}" /tmp/libdvdcss.tar.bz2

COPY native /app/native
RUN gcc -shared -fPIC -o /usr/local/lib/libhitsave_css_unscramble.so.0 /app/native/css_unscramble.c \
    && ln -sf libhitsave_css_unscramble.so.0 /usr/local/lib/libhitsave_css_unscramble.so \
    && ldconfig

RUN apt-get purge -y --auto-remove build-essential gcc libtool autoconf automake pkg-config \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 --shell /bin/bash preview

WORKDIR /app
COPY scripts/extract_dvd_menu_preview.py scripts/dvd_css.py scripts/frame_score.py /app/
COPY config/dvd-menu-preview.yaml /config/dvd-menu-preview.yaml
COPY scripts/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV LD_LIBRARY_PATH=/usr/local/lib

ENTRYPOINT ["/app/entrypoint.sh"]
