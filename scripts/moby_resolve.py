#!/usr/bin/env python3
"""Resolve MobyGames metadata for a press folder title (minimal v1 for test ingest)."""
from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

try:
    import requests
    import yaml
except ImportError:
    raise

from normalize_display_title import normalize_name_fragment

# MobyGames API terms: required suffix wherever API-sourced catalog data is shown.
MOBY_ATTRIBUTION_SUFFIX = "Data by MobyGames.com"


def uses_moby_catalog_data(metadata_json: dict | None) -> bool:
    return bool(metadata_json and metadata_json.get("moby_game_id"))


class _HTMLPlainText(HTMLParser):
    _BLOCK_END = frozenset({"p", "div", "li", "h1", "h2", "h3", "h4", "tr", "blockquote"})

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK_END:
            self._parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)


def html_to_plain_text(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "<" not in raw and ">" not in raw:
        return re.sub(r"\n{3,}", "\n\n", raw).strip()
    parser = _HTMLPlainText()
    parser.feed(raw)
    parser.close()
    text = html.unescape("".join(parser._parts))
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def moby_catalog_fields(metadata_json: dict) -> list[str]:
    fields: list[str] = []
    desc = (metadata_json.get("description_plain") or metadata_json.get("description") or "").strip()
    if desc:
        fields.append("description")
    if metadata_json.get("publishers"):
        fields.append("publisher")
    if metadata_json.get("developers"):
        fields.append("developer")
    if metadata_json.get("first_release_date"):
        fields.append("first_release_date")
    return fields


def build_moby_attribution_sentence(metadata_json: dict) -> str:
    field_labels = {
        "description": "Description",
        "publisher": "Publisher",
        "developer": "Developer",
        "first_release_date": "Release date",
    }
    fields = metadata_json.get("moby_catalog_fields") or moby_catalog_fields(metadata_json)
    phrases: list[str] = []
    for key in ("description", "publisher", "developer", "first_release_date"):
        if key in fields:
            phrases.append(field_labels[key])
    if not phrases:
        return "Catalog information from MobyGames."
    if len(phrases) == 1:
        body = phrases[0]
    elif len(phrases) == 2:
        body = f"{phrases[0]} and {phrases[1]}"
    else:
        body = ", ".join(phrases[:-1]) + f", and {phrases[-1]}"
    return f"{body} information from MobyGames."


def moby_attribution_suffix(moby_cfg: dict | None = None) -> str:
    if moby_cfg:
        suffix = moby_cfg.get("attribution_suffix") or moby_cfg.get("attribution")
        if suffix:
            return str(suffix).strip()
    return MOBY_ATTRIBUTION_SUFFIX


def moby_attribution_text(metadata_json: dict | None, moby_cfg: dict | None = None) -> str:
    if not metadata_json:
        return moby_attribution_suffix(moby_cfg)
    sentence = build_moby_attribution_sentence(metadata_json)
    suffix = moby_attribution_suffix(moby_cfg)
    if not suffix:
        return sentence.strip()
    # Sentence already credits MobyGames (e.g. “… from MobyGames.”); skip redundant suffix.
    if "mobygames" in sentence.casefold():
        return sentence.strip()
    if suffix.casefold() in sentence.casefold():
        return sentence.strip()
    return f"{sentence} {suffix}".strip()


def moby_description_plain(metadata_json: dict) -> str:
    plain = (metadata_json.get("description_plain") or "").strip()
    if plain:
        return plain
    return html_to_plain_text(metadata_json.get("description") or "")


def moby_description_for_omeka(metadata_json: dict, moby_cfg: dict | None = None) -> str:
    """Plain synopsis only; Moby attribution belongs on dcterms:rights."""
    _ = moby_cfg
    plain = moby_description_plain(metadata_json)
    if not plain:
        return ""
    credit = moby_attribution_text(metadata_json, moby_cfg)
    if credit and credit.casefold() in plain.casefold():
        # Strip a trailing attribution block from older ledger/API payloads.
        idx = plain.casefold().rfind(credit.casefold())
        if idx != -1:
            plain = plain[:idx].rstrip()
    return plain


def choose_platform_ids(platforms: list[dict], first_release_date: str | None, scope: str) -> list[int]:
    ids: list[int] = []
    for plat in platforms:
        pid = plat.get("platform_id")
        if pid is not None:
            ids.append(int(pid))
    if not ids:
        return []
    if scope == "all":
        return ids
    if first_release_date:
        for plat in platforms:
            if str(plat.get("first_release_date") or "") == first_release_date and plat.get("platform_id"):
                return [int(plat["platform_id"])]
    dated = [p for p in platforms if p.get("first_release_date") and p.get("platform_id")]
    if dated:
        best = min(dated, key=lambda p: str(p["first_release_date"]))
        return [int(best["platform_id"])]
    return [ids[0]]


def companies_from_platform_detail(detail: dict) -> tuple[list[str], list[str]]:
    publishers: list[str] = []
    developers: list[str] = []
    seen_pub: set[str] = set()
    seen_dev: set[str] = set()
    for release in detail.get("releases") or []:
        for company in release.get("companies") or []:
            name = (company.get("company_name") or "").strip()
            role = (company.get("role") or "").casefold()
            if not name:
                continue
            if "publish" in role:
                if name not in seen_pub:
                    seen_pub.add(name)
                    publishers.append(name)
            elif "develop" in role:
                if name not in seen_dev:
                    seen_dev.add(name)
                    developers.append(name)
    return publishers, developers


def finalize_moby_metadata(metadata: dict) -> dict:
    desc = (metadata.get("description") or "").strip()
    if desc:
        metadata["description_plain"] = html_to_plain_text(desc)
    metadata["moby_catalog_fields"] = moby_catalog_fields(metadata)
    metadata["attribution"] = moby_attribution_text(metadata)
    return metadata


@dataclass
class MobyResult:
    status: str
    block_reason: str | None
    moby_game_id: int | None
    moby_title: str | None
    metadata_json: dict | None
    metadata_source: str | None


def load_moby_config(config_root: Path) -> dict:
    path = config_root / "mobygames.yaml"
    if not path.is_file():
        return {"mobygames": {"enabled": False}}
    return yaml.safe_load(path.read_text())


def search_title_from_folder(folder_name: str, display_title: str | None) -> str:
    if display_title and " — " in display_title:
        main = display_title.split(" — ", 1)[0]
        return normalize_name_fragment(main)
    return normalize_name_fragment(folder_name)


class MobyClient:
    def __init__(self, cfg: dict, credentials_path: Path):
        self.cfg = cfg
        self.base = cfg.get("api_base", "https://api.mobygames.com/v1").rstrip("/")
        self.interval = float(cfg.get("min_request_interval_seconds", 1.1))
        self._last_request = 0.0
        self.api_key = ""
        if credentials_path.is_file():
            creds = yaml.safe_load(credentials_path.read_text())
            self.api_key = (creds.get("api_key") or "").strip()

    def _wait_rate(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_request = time.time()

    def _get(self, path: str, params: dict | None = None) -> Any:
        if not self.api_key or self.api_key == "REPLACE_ME":
            raise RuntimeError("moby_credentials_missing")
        self._wait_rate()
        q = dict(params or {})
        q["api_key"] = self.api_key
        url = f"{self.base}/{path.lstrip('/')}"
        resp = requests.get(url, params=q, timeout=60)
        if resp.status_code == 429:
            time.sleep(5)
            resp = requests.get(url, params=q, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def resolve(self, search_title: str) -> MobyResult:
        try:
            data = self._get("games", {"title": search_title, "format": "brief"})
        except Exception as e:
            return MobyResult("error", "moby_api_error", None, None, {"error": str(e)}, None)

        games = data if isinstance(data, list) else data.get("games") or data.get("results") or []
        if not games:
            return MobyResult("no_match", "moby_no_match", None, None, None, None)

        normalized_query = search_title.casefold()
        exact = [g for g in games if (g.get("title") or "").casefold() == normalized_query]
        chosen = exact[0] if len(exact) == 1 else (games[0] if len(games) == 1 else None)
        if chosen is None:
            return MobyResult(
                "ambiguous",
                "moby_ambiguous",
                None,
                None,
                {"candidates": [{"id": g.get("game_id"), "title": g.get("title")} for g in games[:5]]},
                None,
            )

        game_id = int(chosen.get("game_id") or chosen.get("id"))
        return self.resolve_by_id(game_id, search_title, fallback_title=chosen.get("title"))

    def resolve_by_id(
        self,
        game_id: int,
        search_title: str = "",
        *,
        fallback_title: str | None = None,
    ) -> MobyResult:
        try:
            detail = self._get(f"games/{game_id}", {"format": "normal"})
        except Exception as e:
            return MobyResult("error", "moby_api_error", game_id, fallback_title, {"error": str(e)}, None)

        game = detail if isinstance(detail, dict) and detail.get("title") else detail.get("game") or detail
        title = game.get("title") or fallback_title
        description = (game.get("description") or "").strip()
        moby_url = f"https://www.mobygames.com/game/{game_id}"
        metadata = {
            "moby_game_id": game_id,
            "moby_title": title,
            "moby_url": moby_url,
            "description": description,
            "search_title": search_title or title,
        }
        platforms = game.get("platforms") or []
        dates = []
        for plat in platforms:
            for key in ("first_release_date", "release_date"):
                if plat.get(key):
                    dates.append(str(plat[key]))
        if dates:
            metadata["first_release_date"] = sorted(dates)[0]

        scope = self.cfg.get("company_platform_scope", "earliest_platform")
        platform_ids = choose_platform_ids(
            platforms, metadata.get("first_release_date"), scope
        )
        all_publishers: list[str] = []
        all_developers: list[str] = []
        seen_pub: set[str] = set()
        seen_dev: set[str] = set()
        for platform_id in platform_ids:
            try:
                plat_detail = self._get(f"games/{game_id}/platforms/{platform_id}")
            except Exception:
                continue
            pubs, devs = companies_from_platform_detail(plat_detail)
            for name in pubs:
                if name not in seen_pub:
                    seen_pub.add(name)
                    all_publishers.append(name)
            for name in devs:
                if name not in seen_dev:
                    seen_dev.add(name)
                    all_developers.append(name)
        if all_publishers:
            metadata["publishers"] = all_publishers
        if all_developers:
            metadata["developers"] = all_developers

        finalize_moby_metadata(metadata)

        if not description:
            return MobyResult(
                "incomplete",
                "moby_metadata_incomplete",
                game_id,
                title,
                metadata,
                "first_release",
            )

        return MobyResult("ok", None, game_id, title, metadata, "first_release")


def resolve_for_game(
    *,
    config_root: Path,
    folder_name: str,
    display_title: str | None = None,
    moby_game_id: int | None = None,
    moby_search_title: str | None = None,
) -> MobyResult:
    root_cfg = load_moby_config(config_root)
    moby_cfg = root_cfg.get("mobygames") or {}
    if not moby_cfg.get("enabled", True):
        return MobyResult("skipped", None, None, None, None, None)

    cred_rel = Path(moby_cfg.get("credentials_file", "config/mobygames-credentials.yaml"))
    if cred_rel.parts and cred_rel.parts[0] == "config":
        cred_rel = Path(*cred_rel.parts[1:])
    cred_path = config_root / cred_rel
    if not cred_path.is_file():
        cred_path = config_root.parent / moby_cfg.get("credentials_file", "")

    if moby_search_title:
        search = normalize_name_fragment(moby_search_title)
    else:
        search = search_title_from_folder(folder_name, display_title)
    try:
        client = MobyClient(moby_cfg, cred_path)
        if moby_game_id is not None:
            return client.resolve_by_id(int(moby_game_id), search)
        return client.resolve(search)
    except RuntimeError as e:
        if "moby_credentials_missing" in str(e):
            return MobyResult("skipped", None, None, None, {"note": "no credentials file"}, None)
        return MobyResult("error", "moby_api_error", None, None, {"error": str(e)}, None)
