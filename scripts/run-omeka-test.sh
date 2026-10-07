#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

bash scripts/ensure-local-config.sh
python3 scripts/sync-docker-config.py
bash scripts/build-fixture-dips.sh

cd omeka-test
docker compose build omeka
docker compose up -d mariadb omeka

echo "Waiting for Omeka at http://localhost:8088 ..."
for i in $(seq 1 60); do
  if curl -fsS "http://localhost:8088/login" >/dev/null 2>&1; then
    echo "Omeka is up."
    break
  fi
  sleep 5
done

docker compose exec -T -u root omeka sh -c 'rm -rf /var/www/html/volume/modules/HitSaveDipViewer /var/www/html/volume/modules/ArchivematicaConnector /var/www/html/volume/modules/Exports /var/www/html/modules/HitSaveDipViewer /var/www/html/modules/ArchivematicaConnector /var/www/html/modules/Exports' || true
docker compose exec -T -u root omeka omeka-s-cli module:install OmekaDipViewer || true
docker compose exec -T omeka php /scripts/ensure-omeka-dip-viewer-upgrade.php || true
docker compose exec -T omeka omeka-s-cli module:activate OmekaDipViewer || true
docker compose exec -T omeka php /scripts/ensure-omeka-dip-viewer-upgrade.php || true
docker compose exec -T omeka omeka-s-cli module:install Common || true
docker compose exec -T omeka omeka-s-cli module:enable Common || true
docker compose exec -T omeka omeka-s-cli module:install Ark || true
docker compose exec -T omeka omeka-s-cli module:enable Ark || true
docker compose exec -T omeka php /scripts/ensure-omeka-ark-module.php || true
docker compose exec -T -u root omeka php /scripts/ensure-batch-listing-site-page.php || true

PUBLIC_URL="$(python3 -c "import yaml; print(yaml.safe_load(open('config/omeka-test/settings.yaml'))['omeka'].get('public_url','http://localhost:8088').rstrip('/'))")"
echo "Admin: ${PUBLIC_URL}/admin (see config/omeka-test/settings.yaml for credentials)"
echo "Fixture DIP: fixtures/dips/built/sample-game.tar"
