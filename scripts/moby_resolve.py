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
OFFICIAL_DESCRIPTION_LABEL = "Official description (ad blurb)"
# Release notes from Moby platform API — not marketing ad blurbs.
_RELEASE_DESCRIPTION_SKIP = frozenset(
    {
        "download release",
        "digital release",
        "playstation store release",
        "xbox live release",
    }
)


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
    ad = (metadata_json.get("official_ad_blurb_plain") or metadata_json.get("official_ad_blurb") or "").strip()
    if ad and metadata_json.get("description_source") == "ad_blurb":
        fields.append("official_description")
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
        "official_description": OFFICIAL_DESCRIPTION_LABEL,
        "publisher": "Publisher",
        "developer": "Developer",
        "first_release_date": "Release date",
    }
    fields = metadata_json.get("moby_catalog_fields") or moby_catalog_fields(metadata_json)
    phrases: list[str] = []
    for key in ("description", "official_description", "publisher", "developer", "first_release_date"):
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
    synopsis = html_to_plain_text(metadata_json.get("description") or "")
    if synopsis:
        return synopsis
    ad = (metadata_json.get("official_ad_blurb_plain") or "").strip()
    if ad:
        return ad
    return html_to_plain_text(metadata_json.get("official_ad_blurb") or "")


def moby_description_for_omeka(metadata_json: dict, moby_cfg: dict | None = None) -> str:
    """Plain synopsis only; Moby attribution belongs on dcterms:rights."""
    _ = moby_cfg
    plain = moby_description_plain(metadata_json)
    if not plain:
        return ""
    if metadata_json.get("description_source") == "ad_blurb":
        source = (metadata_json.get("official_ad_blurb_source") or "MobyGames").strip()
        header = OFFICIAL_DESCRIPTION_LABEL
        if source:
            header = f"{OFFICIAL_DESCRIPTION_LABEL} ({source})"
        plain = f"{header}\n\n{plain}"
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


def _ad_blurb_from_api_payload(data: Any) -> tuple[str, str]:
    if not data:
        return "", ""
    rows = data if isinstance(data, list) else (
        data.get("ad_blurbs")
        or data.get("official_descriptions")
        or data.get("descriptions")
        or []
    )
    best = ""
    best_source = ""
    for row in rows:
        if isinstance(row, str):
            text = row.strip()
            source = ""
        else:
            text = (row.get("text") or row.get("description") or row.get("body") or "").strip()
            source = (row.get("source") or row.get("source_name") or row.get("label") or "").strip()
        if len(text) > len(best):
            best = text
            best_source = source
    return best, best_source


def _moby_page_cookie_jar(moby_cfg: dict) -> Path | None:
    rel = moby_cfg.get("page_cookie_jar")
    if not rel:
        return None
    path = Path(str(rel))
    if path.parts and path.parts[0] == "config":
        path = Path("/config") / Path(*path.parts[1:])
    elif not path.is_absolute():
        path = Path("/config") / path
    return path if path.is_file() else None


def fetch_official_ad_blurb(
    client: "MobyClient",
    game_id: int,
    platform_ids: list[int],
    moby_cfg: dict | None = None,
    *,
    moby_url: str | None = None,
) -> tuple[str, str]:
    """Best-effort official ad blurb (Moby catalog or platform release text)."""
    moby_cfg = moby_cfg or {}
    min_chars = int(moby_cfg.get("ad_blurb_min_chars", 80))
    best = ""
    best_source = ""
    if moby_cfg.get("fetch_ad_blurbs_api", True):
        try:
            text, source = _ad_blurb_from_api_payload(client._get(f"games/{game_id}/ad-blurbs"))
            if len(text) >= min_chars and len(text) > len(best):
                best, best_source = text, source or "MobyGames"
        except Exception:
            pass
    for platform_id in platform_ids:
        try:
            plat_detail = client._get(f"games/{game_id}/platforms/{platform_id}")
        except Exception:
            continue
        text, source = _ad_blurb_from_api_payload(plat_detail)
        if len(text) >= min_chars and len(text) > len(best):
            best, best_source = text, source or "MobyGames"
        for release in plat_detail.get("releases") or []:
            desc = (release.get("description") or "").strip()
            if len(desc) < min_chars or desc.casefold() in _RELEASE_DESCRIPTION_SKIP:
                continue
            countries = release.get("countries") or []
            source = "MobyGames platform release"
            if countries:
                source = f"MobyGames ({', '.join(countries)})"
            if len(desc) > len(best):
                best, best_source = desc, source
    if (
        len(best) < min_chars
        and moby_cfg.get("fetch_ad_blurbs_html", True)
        and moby_url
    ):
        from moby_page_scrape import fetch_ad_blurb_from_web

        text, source = fetch_ad_blurb_from_web(
            moby_url,
            cookie_jar=_moby_page_cookie_jar(moby_cfg),
            min_chars=min_chars,
        )
        if len(text) > len(best):
            best, best_source = text, source
    return best, best_source


def attach_official_ad_blurb(metadata: dict, client: "MobyClient", platform_ids: list[int], moby_cfg: dict) -> None:
    has_synopsis = bool((metadata.get("description") or "").strip())
    if has_synopsis:
        return
    text, source = fetch_official_ad_blurb(
        client,
        int(metadata["moby_game_id"]),
        platform_ids,
        moby_cfg,
        moby_url=metadata.get("moby_url"),
    )
    if not text:
        return
    metadata["official_ad_blurb"] = text
    metadata["official_ad_blurb_plain"] = html_to_plain_text(text)
    metadata["official_ad_blurb_source"] = source or "MobyGames"
    metadata["description_source"] = "ad_blurb"


def finalize_moby_metadata(metadata: dict) -> dict:
    desc = (metadata.get("description") or "").strip()
    if desc:
        metadata["description_plain"] = html_to_plain_text(desc)
    ad = (metadata.get("official_ad_blurb") or "").strip()
    if ad and not metadata.get("official_ad_blurb_plain"):
        metadata["official_ad_blurb_plain"] = html_to_plain_text(ad)
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


def moby_search_title_candidates(primary: str) -> list[str]:
    """Alternate Moby search strings when folder or press titles differ from Moby catalog spelling."""
    candidates: list[str] = []
    seen: set[str] = set()

    def add(title: str) -> None:
        text = re.sub(r"\s+", " ", (title or "").strip())
        if text and text not in seen:
            seen.add(text)
            candidates.append(text)

    add(primary)
    if re.search(r"(?i)\bchampion edition\b", primary) and not re.search(
        r"(?i)\bchampionship\b", primary
    ):
        add(re.sub(r"(?i)\bchampion edition\b", "Championship Edition", primary))
    if primary.upper().startswith("PAC-"):
        add("Pac" + primary[3:])
    if re.search(r"(?i)\bdynasty warriors gundam reborn\b", primary) and ":" not in primary:
        add(re.sub(r"(?i)\bdynasty warriors gundam reborn\b", "Dynasty Warriors: Gundam Reborn", primary))
    for title in list(candidates):
        if re.search(r"(?i)pac-man.*(?:champion|championship) edition dx", title):
            add("Pac-Man: Championship Edition DX")
            break
    return candidates


def pick_moby_search_game(search_title: str, games: list[dict]) -> dict | None:
    if not games:
        return None
    if len(games) == 1:
        return games[0]
    normalized_query = search_title.casefold()
    exact = [g for g in games if (g.get("title") or "").casefold() == normalized_query]
    if len(exact) == 1:
        return exact[0]
    if "championship edition dx" in normalized_query and "+" not in search_title:
        base = [
            g
            for g in games
            if "+" not in (g.get("title") or "")
            and "championship edition dx" in (g.get("title") or "").casefold()
        ]
        if len(base) == 1:
            return base[0]
    return None


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

        chosen = pick_moby_search_game(search_title, games)
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

        attach_official_ad_blurb(metadata, self, platform_ids, self.cfg)
        finalize_moby_metadata(metadata)

        has_body = bool((metadata.get("description_plain") or metadata.get("official_ad_blurb_plain") or "").strip())
        if not has_body:
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
        best: MobyResult | None = None
        for candidate in moby_search_title_candidates(search):
            result = client.resolve(candidate)
            if result.status in ("ok", "incomplete"):
                return result
            if result.status == "ambiguous" and (
                best is None or best.status in ("no_match", "error")
            ):
                best = result
            if best is None or (best.status == "no_match" and result.moby_game_id):
                best = result
        return best if best is not None else MobyResult("no_match", "moby_no_match", None, None, None, None)
    except RuntimeError as e:
        if "moby_credentials_missing" in str(e):
            return MobyResult("skipped", None, None, None, {"note": "no credentials file"}, None)
        return MobyResult("error", "moby_api_error", None, None, {"error": str(e)}, None)
