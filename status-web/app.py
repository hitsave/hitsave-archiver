#!/usr/bin/env python3
"""Read-only preservation ledger dashboard."""
from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg
import yaml

CONFIG = yaml.safe_load(Path("/config/status-web.yaml").read_text())
DB = yaml.safe_load(Path(CONFIG["database"]["config_file"]).read_text())["postgres"]
HOST = CONFIG["server"]["host"]
PORT = int(CONFIG["server"]["port"])
ADMIN = CONFIG["omeka"]["public_admin_base"].rstrip("/")
SITE = CONFIG["omeka"]["public_site_base"].rstrip("/")


def connect():
    return psycopg.connect(
        host=DB["host"],
        port=int(DB.get("port", 5432)),
        dbname=DB["database"],
        user=DB["user"],
        password=DB["password"],
    )


def fetch_rows(q: str = "") -> list[dict]:
    sql = """
        SELECT game_key, status, moby_status, block_reason, omeka_item_id, omeka_media_id,
               moby_title, source_path, aip_wasabi_prefix, aip_uploaded_at, updated_at
        FROM game_ingest
    """
    params: tuple = ()
    if q:
        sql += " WHERE game_key ILIKE %s OR moby_title ILIKE %s OR source_path ILIKE %s"
        pat = f"%{q}%"
        params = (pat, pat, pat)
    sql += " ORDER BY updated_at DESC LIMIT 200"
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def render_page(q: str = "") -> bytes:
    rows = fetch_rows(q)
    trs = []
    for row in rows:
        item_id = row.get("omeka_item_id")
        admin = f'{ADMIN}/item/{item_id}/show' if item_id else ""
        public = f"{SITE}/item/{item_id}" if item_id else ""
        links = []
        if admin:
            links.append(f'<a href="{html.escape(admin)}">admin</a>')
        if public:
            links.append(f'<a href="{html.escape(public)}">site</a>')
        wasabi = html.escape(str(row.get("aip_wasabi_prefix") or ""))
        trs.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('game_key') or ''))}</td>"
            f"<td>{html.escape(str(row.get('status') or ''))}</td>"
            f"<td>{html.escape(str(row.get('moby_status') or ''))}</td>"
            f"<td>{html.escape(str(row.get('moby_title') or ''))}</td>"
            f"<td>{wasabi}</td>"
            f"<td>{' '.join(links)}</td>"
            f"<td>{html.escape(str(row.get('updated_at') or ''))}</td>"
            "</tr>"
        )
    body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>HitSave ingest status</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 1.5rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 0.35rem 0.5rem; text-align: left; }}
th {{ background: #f4f4f4; }}
</style></head><body>
<h1>HitSave preservation ledger</h1>
<form method="get"><input name="q" value="{html.escape(q)}" size="40" placeholder="Search game key / title">
<button type="submit">Search</button></form>
<p>{len(rows)} rows (max 200)</p>
<table><thead><tr>
<th>Game key</th><th>Status</th><th>Moby</th><th>Moby title</th><th>Wasabi prefix</th><th>Omeka</th><th>Updated</th>
</tr></thead><tbody>
{''.join(trs) if trs else '<tr><td colspan="7">No rows</td></tr>'}
</tbody></table>
<p><small>MobyGames catalog fields in this table: Data by MobyGames.com</small></p>
</body></html>"""
    return body.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/", "/index.html"):
            self.send_response(404)
            self.end_headers()
            return
        q = parse_qs(parsed.query).get("q", [""])[0]
        data = render_page(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        return


if __name__ == "__main__":
    HTTPServer((HOST, PORT), Handler).serve_forever()
