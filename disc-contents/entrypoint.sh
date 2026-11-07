#!/bin/sh
# /output is a host directory Docker may have created as root (first run on a host): hand it to the
# contents user, then run the extractor as that user.
set -e
mkdir -p /output
chown contents:contents /output
exec runuser -u contents -- python3 /app/extract_disc_contents.py "$@"
