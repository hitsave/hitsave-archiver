#!/usr/bin/env python3
"""Post newly published archive items to the HitSave Discord changelog channel.

The ledger records the first time each Omeka item is published (schema/015_publication.sql).
Each run of this job:

  1. finds ledger items that are public in Omeka but have no publication row (published from a
     script or the Omeka admin) and records them ("reconcile");
  2. reads unannounced rows from Omeka, new ones first, then the oldest skipped ones, until
     --max-items are ready to post (at most 200 reads a run): a deleted item is closed without a
     post; an item that is hidden again or still has the placeholder description stays pending
     and moves behind newer rows;
  3. posts the rest as embeds, 10 per message, as the archive bot account, and marks each
     message's items announced right after that message is accepted.

Before the first run, --backfill --execute marks every item that is already public as announced
without posting, so the channel isn't flooded. The job refuses to post until that has run.

    discord_digest.py [--execute] [--max-items 40]
    discord_digest.py --backfill [--execute]

Dry run by default. Run in omeka-catalog (it has the Omeka API config, the ledger settings and
/config/secrets). See docs/discord-digest.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

SITE = "https://archive.hitsave.org"
ITEM_URL = SITE + "/ark:/78322/{id}"
# Omeka's placeholder for items without their own thumbnail: a blank square in Discord.
DEFAULT_THUMBNAIL_SUFFIX = "/application/asset/thumbnails/default.png"
DIP_RENDERER = "omeka_dip_package"
PLACEHOLDER = "More information to be added at a later date."
API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://hitsave.org, 1.0)"
FIRST_CONTENT = "New in the archive"
COLOR = 0x3B82F6
MAX_EMBEDS = 10
MAX_MESSAGE_CHARS = 6000
TITLE_MAX = 256
DESCRIPTION_MAX = 300
MAX_RETRIES = 3
MAX_CHECKS_PER_RUN = 200  # Omeka item reads per run, so skipped items can't make a run endless


class DigestError(Exception):
    pass


# --- building messages ---------------------------------------------------------------------

def _first_value(item: dict, field: str) -> str:
    values = item.get(field) or []
    if isinstance(values, list):
        for v in values:
            if isinstance(v, dict) and str(v.get("@value") or "").strip():
                return str(v["@value"]).strip()
    return ""


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_embed(item: dict, thumbnail: str | None = None) -> dict | None:
    """The Discord embed for one Omeka item, or None while it has the placeholder description."""
    description = _first_value(item, "dcterms:description")
    if description.startswith(PLACEHOLDER):
        return None
    title = str(item.get("o:title") or "").strip() or "Untitled"
    embed: dict = {
        "title": title[:TITLE_MAX],
        "url": ITEM_URL.format(id=item["o:id"]),
        "color": COLOR,
    }
    if description:
        embed["description"] = _cut(description, DESCRIPTION_MAX)
    if thumbnail:
        embed["thumbnail"] = {"url": thumbnail}
    fmt = _first_value(item, "dcterms:format")
    if fmt:
        embed["footer"] = {"text": fmt[:2048]}
    return embed


def _embed_chars(embed: dict) -> int:
    return (len(embed.get("title", "")) + len(embed.get("description", ""))
            + len((embed.get("footer") or {}).get("text", "")))


def chunk_messages(embeds: list[dict]) -> list[list[dict]]:
    """Split embeds into messages of at most 10 embeds and 6000 characters."""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    chars = 0
    for e in embeds:
        n = _embed_chars(e)
        if current and (len(current) == MAX_EMBEDS or chars + n > MAX_MESSAGE_CHARS - len(FIRST_CONTENT)):
            chunks.append(current)
            current, chars = [], 0
        current.append(e)
        chars += n
    if current:
        chunks.append(current)
    return chunks


def post_message(post: Callable, token: str, channel_id: str, embeds: list[dict], *, first: bool,
                 sleep: Callable[[float], None] = time.sleep) -> str:
    """Post one message as the bot and return its id. Errors never contain the token."""
    body = {"content": FIRST_CONTENT if first else "", "embeds": embeds, "allowed_mentions": {"parse": []}}
    headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT, "Content-Type": "application/json"}
    url = f"{API}/channels/{channel_id}/messages"
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = post(url, json=body, headers=headers, timeout=30)
        except Exception as exc:  # noqa: BLE001 - the exception text could carry request details
            raise DigestError(f"couldn't reach Discord ({type(exc).__name__})") from None
        if resp.status_code == 429 and attempt < MAX_RETRIES:
            try:
                wait = float(resp.json().get("retry_after", 1))
            except Exception:  # noqa: BLE001
                wait = 1.0
            sleep(min(max(wait, 0.0), 60.0))
            continue
        if 200 <= resp.status_code < 300:
            return str(resp.json().get("id", ""))
        raise DigestError(f"Discord returned HTTP {resp.status_code}")
    raise DigestError("Discord returned HTTP 429")


# --- ledger --------------------------------------------------------------------------------

class Store:
    """The ledger tables the digest reads and writes. The connection must be in autocommit mode."""

    def __init__(self, conn):
        self.conn = conn

    def try_lock(self) -> bool:
        return bool(self.conn.execute("SELECT pg_try_advisory_lock(hashtext('discord_digest'))").fetchone()[0])

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM digest_state WHERE key = %s", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO digest_state (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (key, value))

    def ledger_item_ids(self) -> set[int]:
        rows = self.conn.execute("SELECT DISTINCT omeka_item_id FROM game_ingest WHERE omeka_item_id IS NOT NULL")
        return {int(r[0]) for r in rows.fetchall()}

    def recorded_ids(self) -> set[int]:
        return {int(r[0]) for r in self.conn.execute("SELECT omeka_item_id FROM publication").fetchall()}

    def insert(self, ids: Iterable[int], source: str, *, announced: bool) -> int:
        """Add rows for ids that have none. Returns how many were added."""
        ids = sorted(ids)
        if not ids:
            return 0
        cur = self.conn.execute(
            "INSERT INTO publication (omeka_item_id, game_ingest_id, published_by, source, announced_at, skip_reason) "
            "SELECT i, (SELECT id FROM game_ingest g WHERE g.omeka_item_id = i ORDER BY id LIMIT 1), %s, %s, "
            "CASE WHEN %s THEN now() END, CASE WHEN %s THEN %s END "
            "FROM unnest(%s::int[]) AS i ON CONFLICT (omeka_item_id) DO NOTHING",
            (source, source, announced, announced, source, ids))
        return cur.rowcount

    def pending(self, limit: int, exclude: Iterable[int] = ()) -> list[int]:
        """Unannounced items: never-checked ones first, oldest published first; then the longest-skipped."""
        rows = self.conn.execute(
            "SELECT omeka_item_id FROM publication WHERE announced_at IS NULL AND NOT (omeka_item_id = ANY(%s)) "
            "ORDER BY last_checked_at NULLS FIRST, published_at, id LIMIT %s", (list(exclude), limit))
        return [int(r[0]) for r in rows.fetchall()]

    def touch(self, ids: list[int]) -> None:
        """Move skipped items behind newer ones."""
        self.conn.execute("UPDATE publication SET last_checked_at = now() WHERE omeka_item_id = ANY(%s)", (list(ids),))

    def mark(self, ids: list[int], *, message_id: str | None = None, skip_reason: str | None = None) -> None:
        self.conn.execute(
            "UPDATE publication SET announced_at = now(), discord_message_id = %s, skip_reason = %s "
            "WHERE omeka_item_id = ANY(%s) AND announced_at IS NULL", (message_id, skip_reason, list(ids)))


# --- Omeka ---------------------------------------------------------------------------------

class Omeka:
    def __init__(self, base: str, creds: dict, session, per_page: int = 1000, max_pages: int = 100):
        self.base = base.rstrip("/")
        self.keys = {"key_identity": creds["key_identity"], "key_credential": creds["key_credential"]}
        self.session = session
        self.per_page = per_page
        self.max_pages = max_pages
        self.headers = {"Accept": "application/json", "User-Agent": "HitSaveDigest/1.0"}

    def public_ids(self) -> set[int]:
        ids: set[int] = set()
        for page in range(1, self.max_pages + 1):
            r = self.session.get(f"{self.base}/items", headers=self.headers, timeout=120,
                                 params={**self.keys, "is_public": "1", "per_page": self.per_page, "page": page,
                                         "sort_by": "id", "sort_order": "asc"})
            if r.status_code != 200:
                raise DigestError(f"Omeka returned HTTP {r.status_code} listing public items")
            data = r.json()
            if not data:
                return ids
            ids.update(int(i["o:id"]) for i in data if isinstance(i, dict) and "o:id" in i)
        raise DigestError(f"more than {self.max_pages} pages of public items; raise max_pages")

    def thumbnail(self, item: dict) -> str | None:
        """A preview image URL for the item, or None.

        Omeka's own thumbnail when the item has one. Archive items usually don't: their media is a
        DIP package, so Omeka returns its blank default.png. Then use the DIP viewer's browse preview,
        the image the archive site shows in item lists, picking the media the way the viewer does
        (DipBrowsePreviewService::findDipMedia): disc contents first, then the primary DIP package.
        The preview is used only if it answers with an image (it is 404 when the package has none).
        """
        thumbs = item.get("thumbnail_display_urls") or {}
        if isinstance(thumbs, dict):
            url = thumbs.get("medium") or thumbs.get("large") or thumbs.get("square")
            if url and not str(url).endswith(DEFAULT_THUMBNAIL_SUFFIX):
                return url
        media_id = self._dip_media_id(item)
        if media_id is None:
            return None
        preview = f"{SITE}/omeka-dip/browse-preview/{media_id}?type=medium"
        try:
            r = self.session.get(preview, headers=self.headers, timeout=120, stream=True)
            ok = r.status_code == 200 and str(r.headers.get("Content-Type", "")).startswith("image/")
            r.close()
        except Exception:  # noqa: BLE001 - no thumbnail is better than no post
            return None
        return preview if ok else None

    def _dip_media_id(self, item: dict) -> int | None:
        refs = [m.get("o:id") for m in item.get("o:media") or [] if isinstance(m, dict) and m.get("o:id")]
        primary = (item.get("o:primary_media") or {}).get("o:id")
        media = {}
        for mid in refs:
            r = self.session.get(f"{self.base}/media/{mid}", headers=self.headers, timeout=60, params=self.keys)
            if r.status_code == 200:
                media[mid] = r.json()
        dip = {mid: m for mid, m in media.items() if m.get("o:renderer") == DIP_RENDERER}
        for mid, m in dip.items():
            data = m.get("data") or m.get("o:data") or {}
            if str(data.get("package_storage_prefix") or "").lstrip("/").startswith("disc-contents/"):
                return mid
        if primary in dip:
            return primary
        return next(iter(dip), None)

    def item(self, item_id: int) -> dict | None:
        """The item, or None when Omeka says it doesn't exist."""
        r = self.session.get(f"{self.base}/items/{item_id}", headers=self.headers, timeout=60, params=self.keys)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise DigestError(f"Omeka returned HTTP {r.status_code} for item {item_id}")
        return r.json()


# --- the job -------------------------------------------------------------------------------

def backfill(store, omeka, *, execute: bool) -> int:
    """Mark every public item without a row as announced, without posting.

    All public items, not only ledger ones: a ledger package can later point at an old public record
    (an ARK merge, a new dump on an existing record), and reconcile must not announce that as new.
    """
    ids = omeka.public_ids() - store.recorded_ids()
    if execute:
        store.insert(ids, "backfill", announced=True)
        store.set_state("backfilled", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return len(ids)


def reconcile(store, public_ids: set[int]) -> int:
    """Record public ledger items that have no publication row yet. Returns how many."""
    ids = (public_ids & store.ledger_item_ids()) - store.recorded_ids()
    return store.insert(ids, "reconcile", announced=False)


def run(store, omeka, post: Callable, discord: dict, *, execute: bool, max_items: int = 40,
        sleep: Callable[[float], None] = time.sleep) -> dict:
    if not store.try_lock():
        print("another digest run holds the lock", flush=True)
        return {"locked": True}
    if not store.get_state("backfilled"):
        raise DigestError("run --backfill first")
    summary = {"pending": 0, "checked": 0, "posted": 0, "would_post": 0, "skipped_hidden": 0, "skipped_deleted": 0,
               "skipped_placeholder": 0, "messages": 0, "reconciled": 0}
    public = omeka.public_ids()
    if execute:
        summary["reconciled"] = reconcile(store, public)
    else:
        summary["reconciled"] = len((public & store.ledger_item_ids()) - store.recorded_ids())
    ready: list[tuple[int, dict]] = []
    deleted: list[int] = []
    skipped: list[int] = []
    checked: list[int] = []
    while len(ready) < max_items and len(checked) < MAX_CHECKS_PER_RUN:
        batch = store.pending(min(max_items, MAX_CHECKS_PER_RUN - len(checked)), exclude=checked)
        if not batch:
            break
        for item_id in batch:
            if len(ready) >= max_items:
                break
            checked.append(item_id)
            it = omeka.item(item_id)
            if it is None:
                deleted.append(item_id)
                summary["skipped_deleted"] += 1
                continue
            if it.get("o:is_public") is not True:
                skipped.append(item_id)
                summary["skipped_hidden"] += 1
                continue
            embed = build_embed(it)
            if embed is None:
                skipped.append(item_id)
                summary["skipped_placeholder"] += 1
                continue
            thumb = omeka.thumbnail(it)
            if thumb:
                embed["thumbnail"] = {"url": thumb}
            ready.append((item_id, embed))
    summary["checked"] = summary["pending"] = len(checked)
    if execute and skipped:
        store.touch(skipped)
    if execute and deleted:
        store.mark(deleted, skip_reason="deleted")
    by_embed = {id(e): i for i, e in ready}
    chunks = chunk_messages([e for _, e in ready])
    if not execute:
        summary["would_post"] = len(ready)
        for e in (e for c in chunks for e in c):
            print(f"WOULD POST {e['url']}  {e['title']}", flush=True)
        return summary
    for n, chunk in enumerate(chunks):
        message_id = post_message(post, discord["token"], discord["channel_id"], chunk, first=(n == 0), sleep=sleep)
        store.mark([by_embed[id(e)] for e in chunk], message_id=message_id)
        summary["messages"] += 1
        summary["posted"] += len(chunk)
        for e in chunk:
            print(f"POSTED {e['url']}  {e['title']}", flush=True)
        if n + 1 < len(chunks):
            sleep(1.0)
    return summary


def main() -> int:
    import psycopg
    import requests

    from omeka_key_redaction import install_omeka_key_redaction
    from yaml_config import load_yaml

    install_omeka_key_redaction()  # Omeka takes the API key as URL parameters; keep it out of tracebacks
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="post and write to the ledger (default: dry run)")
    ap.add_argument("--backfill", action="store_true", help="mark already-public items announced, without posting")
    ap.add_argument("--max-items", type=int, default=40, help="post at most this many items per run")
    args = ap.parse_args()

    config_root = Path(os.environ.get("HITSAVE_CONFIG_ROOT", "/config"))
    api_cfg = load_yaml(config_root / "omeka-catalog.yaml")["api"]
    cred_rel = Path(api_cfg["credentials_file"])
    if cred_rel.parts and cred_rel.parts[0] == "config":
        cred_rel = Path(*cred_rel.parts[1:])
    omeka = Omeka(api_cfg["base_url"], load_yaml(config_root / cred_rel), requests.Session())

    ingest = load_yaml(config_root / "preservation" / "ingest.yaml")
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    conn = psycopg.connect(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
                           user=db["user"], password=db["password"], autocommit=True)
    store = Store(conn)
    try:
        if args.backfill:
            if not store.try_lock():
                print("another digest run holds the lock", flush=True)
                return 0
            n = backfill(store, omeka, execute=args.execute)
            print(json.dumps({"mode": "execute" if args.execute else "dry-run", "backfill": n}))
            return 0
        discord: dict = {}
        if args.execute:
            secret = config_root / "secrets" / "discord-bot.yaml"
            if not secret.is_file():
                raise DigestError("no Discord bot configured")
            cfg = load_yaml(secret)
            discord = {"token": str(cfg["token"]).strip(), "channel_id": str(cfg["channel_id"]).strip()}
        summary = run(store, omeka, requests.post, discord, execute=args.execute, max_items=args.max_items)
        print(json.dumps({"mode": "execute" if args.execute else "dry-run", **summary}))
        return 0
    except DigestError as exc:
        print(f"discord digest: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
