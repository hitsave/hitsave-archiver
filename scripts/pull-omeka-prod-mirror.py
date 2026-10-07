#!/usr/bin/env python3
"""Pull public site + resource template config from production Omeka (read-only)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import requests
    import yaml
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/omeka-test/omeka-prod-source.yaml"
SOURCE_EXAMPLE = ROOT / "config/omeka-test/omeka-prod-source.yaml.example"
OUT = ROOT / "config/omeka-test/mirror/prod-archive.yaml"
OUT_JSON = ROOT / "config/omeka-test/mirror/prod-archive.json"
def load_creds(prod_cfg: dict) -> dict:
    rel = prod_cfg.get("credentials_file", "secrets/omeka-api-credentials-prod.yaml")
    path = ROOT / rel if not Path(rel).is_absolute() else Path(rel)
    return yaml.safe_load(path.read_text())


def get_json(session: requests.Session, url: str, params: dict) -> dict | list:
    resp = session.get(url, params=params, timeout=120)
    resp.raise_for_status()
    return resp.json()


def property_term(session: requests.Session, base: str, params: dict, prop_id: int) -> dict:
    row = get_json(session, f"{base}/properties/{prop_id}", params)
    return {"id": prop_id, "term": row["o:term"], "label": row["o:label"]}


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(
            f"Missing {SOURCE.relative_to(ROOT)} — copy from {SOURCE_EXAMPLE.name} and edit."
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    src = yaml.safe_load(SOURCE.read_text())
    prod = src["production"]
    creds = load_creds(prod)
    params = {"key_identity": creds["key_identity"], "key_credential": creds["key_credential"]}
    base = prod["base_url"].rstrip("/")
    session = requests.Session()

    site = get_json(session, f"{base}/sites/{prod['site_id']}", params)
    page_id_to_slug: dict[int, str] = {}
    rt = get_json(session, f"{base}/resource_templates/{prod['press_resource_template_id']}", params)
    rc_id = rt["o:resource_class"]["o:id"]
    rc = get_json(session, f"{base}/resource_classes/{rc_id}", params)

    template_properties = []
    for row in rt.get("o:resource_template_property", []):
        pid = row["o:property"]["o:id"]
        prop = property_term(session, base, params, pid)
        template_properties.append(
            {
                "term": prop["term"],
                "alternate_label": row.get("o:alternate_label"),
                "required": bool(row.get("o:is_required")),
                "data_type": row.get("o:data_type") or [],
            }
        )

    pages = []
    for ref in site.get("o:page", []):
        page = get_json(session, f"{base}/site_pages/{ref['o:id']}", params)
        page_id_to_slug[page["o:id"]] = page["o:slug"]
        pages.append(
            {
                "prod_id": page["o:id"],
                "slug": page["o:slug"],
                "title": page["o:title"],
                "is_public": page.get("o:is_public", True),
                "layout": page.get("o:layout"),
                "blocks": page.get("o:block", []),
            }
        )

    navigation = []
    for entry in site.get("o:navigation", []):
        entry = dict(entry)
        if entry.get("type") == "page":
            pid = entry.get("data", {}).get("id")
            if pid in page_id_to_slug:
                entry["page_slug"] = page_id_to_slug[pid]
        navigation.append(entry)

    homepage_slug = None
    home_id = (site.get("o:homepage") or {}).get("o:id")
    if home_id in page_id_to_slug:
        homepage_slug = page_id_to_slug[home_id]

    try:
        css_resp = session.get("https://archive.hitsave.org/css-editor", timeout=30)
        css_editor = css_resp.text.strip() if css_resp.ok else ""
    except requests.RequestException:
        css_editor = ""

    mirror = {
        "pulled_from": prod["base_url"],
        "site": {
            "slug": site["o:slug"],
            "title": site["o:title"],
            "theme": site["o:theme"],
            "navigation": navigation,
            "homepage_slug": homepage_slug,
        },
        "theme_settings": {
            "nav_layout": "dropdown",
            "nav_show_levels": 1,
            "nav_depth": 0,
            "truncate_body_property": "ellipsis",
            "footer": (
                "Run by Hit Save!, a 501(c)(3) non-profit dedicated to the preservation "
                "of video games, their history, and related physical and digital materials."
            ),
            "css_editor": css_editor,
            "resource_page_blocks": {
                "items": {
                    # Items: mediaList (OmekaDipViewer overrides partial). mediaRender is media-only.
                    "full_width_main": ["mediaList"],
                    "main": ["values", "itemSets"],
                    "right": ["mediaList"],
                }
            },
        },
        "resource_template": {
            "label": rt["o:label"],
            "resource_class_term": rc["o:term"],
            "properties": template_properties,
        },
        "site_pages": pages,
        "apply_notes": [
            "curation:location (Storage Box) skipped on test unless Curation vocabulary is installed.",
            "Custom vocab data types on dcterms:spatial are stored as literals on test.",
            "Navigation page links are recreated by slug where possible.",
            "DIP items use OmekaDipViewer; mediaList blocks differ from prod LightGallery until viewer parity.",
        ],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(yaml.safe_dump(mirror, sort_keys=False, allow_unicode=True))
    OUT_JSON.write_text(json.dumps(mirror, indent=2) + "\n")
    print(f"Wrote {OUT}")
    print(f"Wrote {OUT_JSON}")
    print(f"  site theme: {mirror['site']['theme']}")
    print(f"  template: {mirror['resource_template']['label']} ({len(template_properties)} properties)")
    print(f"  pages: {len(pages)}")


if __name__ == "__main__":
    main()
