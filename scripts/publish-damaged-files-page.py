#!/usr/bin/env python3
"""Publish the public "Damaged files" page on the archive (an Omeka site page).

Lists every payload file recorded as damaged (inventory_json files[].integrity.status ==
"damaged", from build-bagit-package.py or check-integrity-wasabi.py) for items the public can
see, so people can look for intact copies. An item is listed only if the public Omeka API
(no key) returns it; private items never appear.

    publish-damaged-files-page.py --out DIR [--execute] [--site start] [--slug damaged-files]

Dry run by default: writes DIR/damaged-files.html and prints counts. With --execute, the page
is created if missing, or else its blocks are replaced (backup of the old page JSON in DIR
first). Run in omeka-catalog with -e HITSAVE_CONFIG_ROOT=/config and the omeka-catalog.yaml
mount, plus the ledger config (ingest.yaml).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

DEFAULT_CONTACT = (
    '<a href="https://hitsave.org/contact-us/">contact us</a> or tell us on '
    '<a href="https://discord.gg/TCKT6uA">Discord</a>'
)
CUT_OFF = ("truncat", "startxref", "trailer", "moov atom not found", "unexpected end", "eof", "end of data",
           "end of file")
NOT_THIS_TYPE = ("not a readable", "unknown or corrupt header", "can't open as archive", "not a pdf",
                 "invalid data found when processing input")


def plain_problem(detail: str | None) -> str:
    text = (detail or "").lower()
    if any(m in text for m in CUT_OFF):
        return "Cut off: the end of the file is missing"
    if any(m in text for m in NOT_THIS_TYPE):
        return "Not a readable file of this type"
    return "Damaged data inside the file"


def _type(entry: dict) -> str:
    name = (entry.get("format") or {}).get("name")
    if name:
        return str(name)
    ext = PurePosixPath(str(entry["path"])).suffix.lstrip(".").upper()
    return ext or "Unknown"


def damaged_entries(rows: list[dict], *, item_title: Callable[[int], str | None]) -> list[dict]:
    """[{item_id, title, files: [...]}] for public items with damaged files, sorted by title."""
    items = []
    for row in rows:
        item_id = row.get("omeka_item_id")
        damaged = [f for f in (row.get("inventory_json") or {}).get("files") or []
                   if (f.get("integrity") or {}).get("status") == "damaged"]
        if not item_id or not damaged:
            continue
        title = item_title(int(item_id))
        if not title:
            continue
        files = []
        for f in damaged:
            rel = PurePosixPath(str(f["path"]))
            parts = rel.parts[1:] if rel.parts and rel.parts[0] == "data" else rel.parts
            files.append({
                "name": parts[-1],
                "folder": "/".join(parts[:-1]),
                "size_bytes": int(f.get("size_bytes") or 0),
                "type": _type(f),
                "problem": plain_problem(f["integrity"].get("detail")),
                "detail": f["integrity"].get("detail") or "",
            })
        files.sort(key=lambda x: (x["folder"].lower(), x["name"].lower()))
        items.append({"item_id": int(item_id), "title": title, "files": files})
    items.sort(key=lambda i: (i["title"].lower(), i["item_id"]))
    return items


def _size(n: int) -> str:
    for unit, step in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= step:
            value = n / step
            return f"{value:.1f} {unit}" if value < 10 and unit != "KB" else f"{value:.0f} {unit}"
    return f"{n} bytes"


def render_html(items: list[dict], *, updated: str, site_slug: str, contact_html: str) -> str:
    e = html.escape
    total = sum(len(i["files"]) for i in items)
    out = [
        "<p>These files reached us damaged: cut off, or with broken data inside. We keep them exactly as we "
        "received them, and we would like to replace them with intact copies.</p>",
        f"<p>If you have an intact copy of any file below, please {contact_html}. The file name and size "
        "usually identify the file. An intact copy is often larger than the damaged one.</p>",
    ]
    if not items:
        out.append(f"<p>No damaged files are known. Last checked {e(updated)}.</p>")
        return "\n".join(out) + "\n"
    out.append(
        f"<p>{total} damaged file{'s' if total != 1 else ''} in {len(items)} item{'s' if len(items) != 1 else ''}. "
        f"Last updated {e(updated)}.</p>"
    )
    for item in items:
        out.append(f'<h3><a href="/s/{e(site_slug)}/item/{item["item_id"]}">{e(item["title"])}</a></h3>')
        out.append("<table>\n<thead><tr><th>File</th><th>Type</th><th>Size</th><th>Problem</th></tr></thead>\n<tbody>")
        for f in item["files"]:
            folder = f"<br><small>{e(f['folder'])}</small>" if f["folder"] else ""
            out.append(
                f"<tr><td>{e(f['name'])}{folder}</td><td>{e(f['type'])}</td><td>{_size(f['size_bytes'])}</td>"
                f"<td>{e(f['problem'])}<br><small>{e(f['detail'])}</small></td></tr>"
            )
        out.append("</tbody>\n</table>")
    return "\n".join(out) + "\n"


def page_body(*, site_id: int, slug: str, title: str, html: str) -> dict:
    return {
        "o:site": {"o:id": site_id},
        "o:slug": slug,
        "o:title": title,
        "o:block": [
            {"o:layout": "pageTitle", "o:data": []},
            {"o:layout": "html", "o:data": {"html": html}},
        ],
    }


def main() -> None:
    import psycopg
    import requests

    from omeka_catalog_common import load_register_dip_omeka_api
    from yaml_config import load_yaml

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--site", default="start", help="site slug")
    ap.add_argument("--slug", default="damaged-files")
    ap.add_argument("--title", default="Damaged files")
    ap.add_argument("--contact-html", default=DEFAULT_CONTACT)
    args = ap.parse_args()

    config_root = Path(os.environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))
    api = load_register_dip_omeka_api()
    api_cfg = load_yaml(config_root / "omeka-catalog.yaml")["api"]
    cred_path = config_root / api_cfg["credentials_file"]
    if cred_path.parts and cred_path.parts[0] == "config":
        cred_path = config_root / Path(*cred_path.parts[1:])
    creds = load_yaml(cred_path)
    base = api_cfg["base_url"].rstrip("/")
    ingest = load_yaml(config_root / "preservation" / "ingest.yaml")
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]

    with psycopg.connect(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
                         user=db["user"], password=db["password"]) as conn:
        cur = conn.execute(
            "SELECT game_key, omeka_item_id, inventory_json FROM game_ingest "
            "WHERE status = 'complete' AND omeka_item_id IS NOT NULL "
            "AND jsonb_path_exists(inventory_json::jsonb, "
            "'$.files[*].integrity ? (@.status == \"damaged\")') ORDER BY game_key"
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    public = requests.Session()  # no key: only what anyone can see

    def item_title(item_id: int) -> str | None:
        r = public.get(f"{base}/items/{item_id}", timeout=60)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return str(r.json().get("o:title") or "") or None

    items = damaged_entries(rows, item_title=item_title)
    page_html = render_html(items, updated=date.today().isoformat(), site_slug=args.site, contact_html=args.contact_html)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "damaged-files.html").write_text(page_html, encoding="utf-8")
    print(f"{sum(len(i['files']) for i in items)} damaged files in {len(items)} public items "
          f"({len(rows)} ledger rows with damaged files); wrote {args.out / 'damaged-files.html'}")
    if not args.execute:
        return

    session = requests.Session()
    headers = {"Accept": "application/ld+json", "Content-Type": "application/json"}
    sites = session.get(api.api_url(base, "sites", creds), params={"slug": args.site}, timeout=60)
    sites.raise_for_status()
    site = next((s for s in sites.json() if s.get("o:slug") == args.site), None)
    if site is None:
        raise SystemExit(f"no site with slug {args.site!r}")
    pages = session.get(api.api_url(base, "site_pages", creds), params={"site_id": site["o:id"], "per_page": 500},
                        timeout=60)
    pages.raise_for_status()
    existing = next((p for p in pages.json() if p.get("o:slug") == args.slug), None)
    body = page_body(site_id=int(site["o:id"]), slug=args.slug, title=args.title, html=page_html)
    if existing is None:
        r = session.post(api.api_url(base, "site_pages", creds), headers=headers, data=json.dumps(body), timeout=120)
        r.raise_for_status()
        print(f"created page {r.json()['o:id']}: /s/{args.site}/page/{args.slug}")
        return
    backup = args.out / f"site-page-{existing['o:id']}.before.json"
    backup.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    r = session.put(api.api_url(base, f"site_pages/{existing['o:id']}", creds), headers=headers,
                    data=json.dumps(body), timeout=120)
    r.raise_for_status()
    after = session.get(api.api_url(base, f"site_pages/{existing['o:id']}", creds), timeout=60).json()
    if after["o:block"][1]["o:data"].get("html") != page_html:
        raise SystemExit(f"page {existing['o:id']} did not take the new HTML (backup: {backup})")
    print(f"updated page {existing['o:id']}: /s/{args.site}/page/{args.slug} (backup: {backup})")


if __name__ == "__main__":
    main()
