#!/usr/bin/env sh
# Rebuild omeka-test image, recreate container, wait for login, optionally apply prod mirror.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT/omeka-test"

APPLY_MIRROR=1
for arg in "$@"; do
  case "$arg" in
    --no-mirror) APPLY_MIRROR=0 ;;
    -h|--help)
      echo "Usage: $0 [--no-mirror]"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

echo "Building omeka image…"
docker compose build omeka

echo "Recreating omeka container…"
docker compose up -d omeka

PUBLIC_URL="$(python3 -c "import yaml; print(yaml.safe_load(open('$ROOT/config/omeka-test/settings.yaml'))['omeka'].get('public_url','http://127.0.0.1:8088').rstrip('/'))")"
LOGIN_URL="${PUBLIC_URL}/login"

echo "Waiting for Omeka at ${LOGIN_URL} …"
for i in $(seq 1 60); do
  if curl -fsS "$LOGIN_URL" >/dev/null 2>&1; then
    echo "Omeka is up."
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "Timed out waiting for Omeka." >&2
    exit 1
  fi
  sleep 2
done

if [ "$APPLY_MIRROR" -eq 1 ]; then
  if docker compose exec -T omeka test -r /config/omeka-test/mirror/prod-archive.json; then
    echo "Applying prod mirror (theme + nav)…"
    docker compose exec -T omeka php /scripts/apply-omeka-prod-mirror.php
  else
    echo "Skipping mirror apply (missing /config/omeka-test/mirror/prod-archive.json)."
  fi
fi

SITE_SLUG="$(python3 -c "import yaml; print(yaml.safe_load(open('$ROOT/config/omeka-test/settings.yaml'))['omeka'].get('site_slug','hitsave-test'))")"
echo "Done. Public site: ${PUBLIC_URL}/s/${SITE_SLUG}/page/intro"
