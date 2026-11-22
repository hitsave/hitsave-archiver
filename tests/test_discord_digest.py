"""scripts/discord_digest.py: post newly published items to the archive changelog channel."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import discord_digest as dd

T0 = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


class FakeResp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


class FakeStore:
    """In-memory stand-in for dd.Store."""

    def __init__(self, ledger_items=(), backfilled=True, lock=True):
        self.ledger = set(ledger_items)
        self.rows: dict[int, dict] = {}
        self.state = {"backfilled": "2026-11-22T00:00:00Z"} if backfilled else {}
        self.lock = lock
        self.writes = 0
        self.calls_after_lock = 0

    def add(self, item_id, minutes, source="ledger"):
        self.rows[item_id] = {"omeka_item_id": item_id, "published_at": T0 + timedelta(minutes=minutes),
                              "source": source, "announced_at": None, "discord_message_id": None, "skip_reason": None,
                              "last_checked_at": None}

    def try_lock(self):
        return self.lock

    def get_state(self, key):
        self.calls_after_lock += 1
        return self.state.get(key)

    def set_state(self, key, value):
        self.writes += 1
        self.state[key] = value

    def ledger_item_ids(self):
        return set(self.ledger)

    def recorded_ids(self):
        return set(self.rows)

    def insert(self, ids, source, *, announced):
        self.writes += 1
        for i in ids:
            self.rows[i] = {"omeka_item_id": i, "published_at": T0, "source": source,
                            "announced_at": T0 if announced else None, "discord_message_id": None,
                            "skip_reason": "backfill" if announced else None, "last_checked_at": None}
        return len(ids)

    def pending(self, limit, exclude=()):
        rows = sorted((r for r in self.rows.values() if r["announced_at"] is None and r["omeka_item_id"] not in exclude),
                      key=lambda r: (r["last_checked_at"] is not None, r["last_checked_at"] or T0, r["published_at"]))
        return [r["omeka_item_id"] for r in rows[:limit]]

    def touch(self, ids):
        self.writes += 1
        self.clock = getattr(self, "clock", T0) + timedelta(seconds=1)
        for i in ids:
            self.rows[i]["last_checked_at"] = self.clock

    def mark(self, ids, *, message_id=None, skip_reason=None):
        self.writes += 1
        for i in ids:
            self.rows[i].update(announced_at=T0, discord_message_id=message_id, skip_reason=skip_reason)


class FakeOmeka:
    def __init__(self, items=None, public=None, thumbs=None):
        self.items = items or {}
        self.public = set(public or ())
        self.thumbs = thumbs or {}

    def thumbnail(self, it):
        return self.thumbs.get(it["o:id"])

    def public_ids(self):
        return set(self.public)

    def item(self, item_id):
        return self.items.get(item_id)


def item(i, public=True, desc="A game."):
    return {"o:id": i, "o:title": f"Item {i}", "o:is_public": public, "dcterms:description": [{"@value": desc}]}


class FakePost:
    def __init__(self, fail_on=None):
        self.sent = []
        self.fail_on = fail_on

    def __call__(self, url, json, headers, timeout):
        self.sent.append({"url": url, "json": json, "headers": headers})
        if self.fail_on is not None and len(self.sent) == self.fail_on:
            return FakeResp(500)
        return FakeResp(200, {"id": str(1000 + len(self.sent))})


DISCORD = {"token": "tok", "channel_id": "123"}


def test_build_embed_fields():
    it = {"o:id": 31336, "o:title": "Aka", "dcterms:description": [{"@value": "A game."}],
          "dcterms:format": [{"@value": "Nintendo Switch"}]}
    e = dd.build_embed(it, thumbnail="https://x/m.jpg")
    assert e["url"] == "https://archive.hitsave.org/ark:/78322/31336"
    assert e["title"] == "Aka" and e["description"] == "A game."
    assert e["thumbnail"] == {"url": "https://x/m.jpg"} and e["footer"] == {"text": "Nintendo Switch"}


def test_build_embed_minimal_item():
    e = dd.build_embed({"o:id": 7})
    assert e["title"] == "Untitled" and "thumbnail" not in e and "footer" not in e
    assert e["url"].endswith("/7")


def test_placeholder_is_skipped():
    it = {"o:id": 1, "o:title": "X", "dcterms:description": [{"@value": dd.PLACEHOLDER + " Soon."}]}
    assert dd.build_embed(it) is None


def test_embed_limits_and_no_mentions():
    e = dd.build_embed({"o:id": 2, "o:title": "T" * 400, "dcterms:description": [{"@value": "@everyone " + "d" * 5000}]})
    assert len(e["title"]) == 256 and len(e["description"]) <= 300 and e["description"].endswith("…")
    sent = []
    dd.post_message(lambda url, json, headers, timeout: sent.append(json) or FakeResp(200, {"id": "9"}),
                    "tok", "123", [e], first=True)
    assert sent[0]["allowed_mentions"] == {"parse": []} and sent[0]["content"] == "New in the archive"


def test_post_message_request_shape():
    seen = {}

    def post(url, json, headers, timeout):
        seen.update(url=url, headers=headers, json=json)
        return FakeResp(200, {"id": "42"})

    assert dd.post_message(post, "tok", "123", [{"title": "a"}], first=False) == "42"
    assert seen["url"] == "https://discord.com/api/v10/channels/123/messages"
    assert seen["headers"]["Authorization"] == "Bot tok"
    assert seen["headers"]["User-Agent"].startswith("DiscordBot (")
    assert seen["json"]["content"] == ""


def test_chunk_messages_respects_10_and_6000():
    embeds = [{"title": "t", "description": "d" * 1000} for _ in range(25)]
    chunks = dd.chunk_messages(embeds)
    assert all(len(c) <= 10 for c in chunks)
    assert all(sum(len(x["title"]) + len(x["description"]) for x in c) <= 6000 for c in chunks)
    assert sum(len(c) for c in chunks) == 25
    assert dd.chunk_messages([]) == []


def test_429_waits_and_retries():
    resps = [FakeResp(429, {"retry_after": 1.5}), FakeResp(200, {"id": "5"})]
    slept = []
    mid = dd.post_message(lambda url, json, headers, timeout: resps.pop(0), "tok", "123", [{"title": "a"}],
                          first=True, sleep=slept.append)
    assert mid == "5" and slept == [1.5]


def test_429_gives_up_after_three_retries():
    with pytest.raises(dd.DigestError):
        dd.post_message(lambda url, json, headers, timeout: FakeResp(429, {"retry_after": 0.1}), "tok", "123",
                        [{"title": "a"}], first=True, sleep=lambda s: None)


def test_token_never_in_error():
    with pytest.raises(dd.DigestError) as exc:
        dd.post_message(lambda url, json, headers, timeout: FakeResp(500), "tok-secret", "123", [{"title": "a"}],
                        first=True)
    assert str(exc.value) == "Discord returned HTTP 500"
    assert "tok-secret" not in str(exc.value)


def test_refuses_without_backfill():
    with pytest.raises(dd.DigestError, match="run --backfill first"):
        dd.run(FakeStore(backfilled=False), FakeOmeka(), FakePost(), DISCORD, execute=True)


def test_backfill_marks_public_items_announced():
    store = FakeStore(ledger_items={5, 6, 7}, backfilled=False)
    assert dd.backfill(store, FakeOmeka(public={5, 6, 99}), execute=True) == 3
    assert {i for i, r in store.rows.items() if r["announced_at"]} == {5, 6, 99}
    assert store.rows[5]["source"] == "backfill"
    assert "backfilled" in store.state


def test_backfill_dry_run_writes_nothing():
    store = FakeStore(ledger_items={5}, backfilled=False)
    assert dd.backfill(store, FakeOmeka(public={5}), execute=False) == 1
    assert store.writes == 0 and not store.rows


def test_reconcile_adds_only_ledger_items():
    store = FakeStore(ledger_items={5, 6})
    store.add(6, 0)
    assert dd.reconcile(store, {5, 6, 99}) == 1
    assert store.rows[5]["source"] == "reconcile" and store.rows[5]["announced_at"] is None
    assert 99 not in store.rows


def test_posts_pending_and_marks_them():
    store = FakeStore(ledger_items={1, 2})
    store.add(1, 0)
    store.add(2, 1)
    post = FakePost()
    s = dd.run(store, FakeOmeka(items={1: item(1), 2: item(2)}, public={1, 2}), post, DISCORD, execute=True)
    assert s["posted"] == 2 and s["messages"] == 1
    assert store.rows[1]["discord_message_id"] == "1001" and store.rows[2]["announced_at"] == T0
    assert [e["title"] for e in post.sent[0]["json"]["embeds"]] == ["Item 1", "Item 2"]


def test_hidden_and_deleted_items():
    store = FakeStore(ledger_items={1, 2})
    store.add(1, 0)
    store.add(2, 1)
    post = FakePost()
    s = dd.run(store, FakeOmeka(items={2: item(2, public=False)}, public=set()), post, DISCORD, execute=True)
    assert s["skipped_deleted"] == 1 and s["skipped_hidden"] == 1 and s["posted"] == 0
    assert store.rows[1]["skip_reason"] == "deleted" and store.rows[1]["announced_at"] == T0
    assert store.rows[2]["announced_at"] is None
    assert post.sent == []


def test_placeholder_stays_pending():
    store = FakeStore(ledger_items={1})
    store.add(1, 0)
    s = dd.run(store, FakeOmeka(items={1: item(1, desc=dd.PLACEHOLDER)}, public={1}), FakePost(), DISCORD,
               execute=True)
    assert s["skipped_placeholder"] == 1 and store.rows[1]["announced_at"] is None


def test_failed_chunk_leaves_rest_pending():
    ids = list(range(1, 13))
    store = FakeStore(ledger_items=ids)
    for i in ids:
        store.add(i, i)
    with pytest.raises(dd.DigestError):
        dd.run(store, FakeOmeka(items={i: item(i) for i in ids}, public=set(ids)), FakePost(fail_on=2), DISCORD,
               execute=True)
    assert all(store.rows[i]["announced_at"] == T0 for i in range(1, 11))
    assert all(store.rows[i]["announced_at"] is None for i in (11, 12))


def test_max_items_carries_over():
    ids = list(range(1, 51))
    store = FakeStore(ledger_items=ids)
    for i in ids:
        store.add(i, i)
    s = dd.run(store, FakeOmeka(items={i: item(i) for i in ids}, public=set(ids)), FakePost(), DISCORD,
               execute=True, max_items=40)
    assert s["posted"] == 40 and s["messages"] == 4
    assert {i for i, r in store.rows.items() if r["announced_at"] is None} == set(range(41, 51))


def test_only_first_message_has_content():
    ids = list(range(1, 13))
    store = FakeStore(ledger_items=ids)
    for i in ids:
        store.add(i, i)
    post = FakePost()
    dd.run(store, FakeOmeka(items={i: item(i) for i in ids}, public=set(ids)), post, DISCORD, execute=True)
    assert [m["json"]["content"] for m in post.sent] == ["New in the archive", ""]


def test_dry_run_writes_nothing():
    store = FakeStore(ledger_items={1, 2})
    store.add(1, 0)
    post = FakePost()
    s = dd.run(store, FakeOmeka(items={1: item(1), 2: item(2)}, public={1, 2}), post, DISCORD, execute=False)
    assert store.writes == 0 and post.sent == []
    assert s["posted"] == 0 and s["would_post"] == 1 and s["reconciled"] == 1


def test_second_run_skips_on_lock():
    store = FakeStore(lock=False)
    assert dd.run(store, FakeOmeka(), FakePost(), DISCORD, execute=True) == {"locked": True}
    assert store.calls_after_lock == 0


def test_items_that_never_get_ready_do_not_block_the_queue():
    ids = list(range(1, 46))
    store = FakeStore(ledger_items=ids + [100])
    for i in ids:
        store.add(i, i)
    store.add(100, 1000)
    items = {i: item(i, desc=dd.PLACEHOLDER) for i in ids}
    items[100] = item(100)
    post = FakePost()
    s = dd.run(store, FakeOmeka(items=items, public=set(items)), post, DISCORD, execute=True, max_items=40)
    assert s["posted"] == 1 and store.rows[100]["announced_at"] == T0
    assert s["skipped_placeholder"] == 45


def test_skipped_rows_move_behind_new_ones():
    store = FakeStore(ledger_items={1, 2})
    store.add(1, 0)
    om = FakeOmeka(items={1: item(1, public=False)}, public=set())
    dd.run(store, om, FakePost(), DISCORD, execute=True)
    store.add(2, 5)
    om.items[2] = item(2)
    om.public.add(2)
    assert store.pending(1) == [2]


def test_check_budget_bounds_a_run():
    ids = list(range(1, 301))
    store = FakeStore(ledger_items=ids)
    for i in ids:
        store.add(i, i)
    om = FakeOmeka(items={i: item(i, public=False) for i in ids}, public=set())
    s = dd.run(store, om, FakePost(), DISCORD, execute=True, max_items=40)
    assert s["checked"] == dd.MAX_CHECKS_PER_RUN and s["posted"] == 0


def test_run_uses_omeka_thumbnail_lookup():
    store = FakeStore(ledger_items={1, 2})
    store.add(1, 0)
    store.add(2, 1)
    post = FakePost()
    om = FakeOmeka(items={1: item(1), 2: item(2)}, public={1, 2}, thumbs={1: "https://p/1.png"})
    dd.run(store, om, post, DISCORD, execute=True)
    embeds = post.sent[0]["json"]["embeds"]
    assert embeds[0]["thumbnail"] == {"url": "https://p/1.png"} and "thumbnail" not in embeds[1]


class FakeHTTP:
    """requests.Session stand-in: maps URL (without query) to (status, content_type, json)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, headers=None, timeout=None, params=None, stream=False):
        self.calls.append(url)
        status, ctype, body = self.routes.get(url.split("?")[0], (404, "text/html", None))

        class R:
            status_code = status
            headers = {"Content-Type": ctype}

            def json(self):
                return body

            def close(self):
                pass

        return R()


DEFAULT = "https://archive.hitsave.org/application/asset/thumbnails/default.png"
API_BASE = "https://archive.hitsave.org/api"


def omeka_with(routes):
    return dd.Omeka(API_BASE, {"key_identity": "i", "key_credential": "c"}, FakeHTTP(routes))


def test_real_omeka_thumbnail_is_kept():
    om = omeka_with({})
    it = {"o:id": 1, "thumbnail_display_urls": {"medium": "https://archive.hitsave.org/files/medium/x.jpg"}}
    assert om.thumbnail(it) == "https://archive.hitsave.org/files/medium/x.jpg"


def test_default_thumbnail_falls_back_to_dip_browse_preview():
    preview = "https://archive.hitsave.org/omeka-dip/browse-preview/11"
    om = omeka_with({
        f"{API_BASE}/media/11": (200, "application/json", {"o:id": 11, "o:renderer": "omeka_dip_package",
                                                          "data": {"package_storage_prefix": "dumps/x"}}),
        preview: (200, "image/png", None),
    })
    it = {"o:id": 1, "thumbnail_display_urls": {"medium": DEFAULT}, "o:primary_media": {"o:id": 11},
          "o:media": [{"o:id": 11}]}
    assert om.thumbnail(it) == preview + "?type=medium"


def test_disc_contents_media_is_preferred():
    om = omeka_with({
        f"{API_BASE}/media/11": (200, "application/json", {"o:id": 11, "o:renderer": "omeka_dip_package",
                                                          "data": {"package_storage_prefix": "dumps/x"}}),
        f"{API_BASE}/media/12": (200, "application/json", {"o:id": 12, "o:renderer": "omeka_dip_package",
                                                          "data": {"package_storage_prefix": "disc-contents/x"}}),
        "https://archive.hitsave.org/omeka-dip/browse-preview/12": (200, "image/png", None),
    })
    it = {"o:id": 1, "thumbnail_display_urls": {"medium": DEFAULT}, "o:primary_media": {"o:id": 11},
          "o:media": [{"o:id": 11}, {"o:id": 12}]}
    assert om.thumbnail(it).startswith("https://archive.hitsave.org/omeka-dip/browse-preview/12?")


def test_no_preview_means_no_thumbnail():
    om = omeka_with({
        f"{API_BASE}/media/11": (200, "application/json", {"o:id": 11, "o:renderer": "omeka_dip_package",
                                                          "data": {"package_storage_prefix": "dumps/x"}}),
    })  # browse-preview answers 404
    it = {"o:id": 1, "thumbnail_display_urls": {"medium": DEFAULT}, "o:primary_media": {"o:id": 11},
          "o:media": [{"o:id": 11}]}
    assert om.thumbnail(it) is None
    assert om.thumbnail({"o:id": 2, "thumbnail_display_urls": {"medium": DEFAULT}, "o:media": []}) is None
