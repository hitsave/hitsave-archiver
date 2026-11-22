# Discord Publication Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Post newly published archive items to the HitSave Discord channel every 3 hours during the day, based on publication records in the ledger. This retires the broken `omeka-discord-bot` on mario.

**Architecture:**
- The ledger records each item the first time it is published. A row goes into a new `publication` table when a publish from the ledger UI succeeds.
- A reconcile step in the digest job catches items published some other way.
- A Windmill job runs at 09:00, 12:00, 15:00, 18:00 and 21:00 Pacific. Each run reads unannounced publications, rechecks each item in Omeka, and posts embeds to the archive changelog channel as the existing bot account, through Discord's REST API. It then marks the posted items as announced.

**Tech Stack:**
- Python 3 (psycopg 3, requests) in the `omeka-catalog` image;
- PostgreSQL ledger;
- Windmill bash script and schedule;
- the Discord REST API (bot token, `POST /channels/{id}/messages`).

**Spec:** The decisions from the 2026-11-22 conversation, copied here because no separate spec doc exists:
- The ledger, not Omeka's creation date, decides what is new.
- Delivery is a digest posted through Discord's REST API, not a running bot.
- It reuses the old bot's account, "Hit Save Archive Announcer". A 2026-11-22 test showed:
  - the token is valid;
  - in the channel the bot can view, send messages and embed links;
  - it can't manage webhooks, so a webhook can't be reused or created.
- It runs about every 3 hours with a pause overnight.
- The old bot on mario is retired.

The old bot was broken in four ways, recorded in memory `discord-bot-mario`. The fourth is the design flaw this plan removes: items are created private (`default_is_public: false`) and published days later, so a filter on creation date never sees them.

## Global Constraints

- Public repo: no bot token or channel id in code, tests or docs. They live only in private config: `hitsave-archiver-config/secrets/discord-bot.yaml`, keys `token` and `channel_id`, mounted at `/config/secrets/discord-bot.yaml` in `omeka-catalog`. The values are copied from the old bot's `.env` on mario.
- Never log or print the token, including in exceptions. Error messages name the HTTP status only.
- Item link: `https://archive.hitsave.org/ark:/78322/<omeka_item_id>`. This is the format the old bot and the ARK rule use.
- Each item is announced once: one `publication` row per Omeka item. Publishing again after hiding the item never posts it again.
- Don't announce items whose first `dcterms:description` value starts with `More information to be added at a later date.` (the old bot's filter). They stay pending until the description changes.
- Discord limits:
  - at most 10 embeds per message;
  - embed title at most 256 characters, embed description at most 4096;
  - at most 6000 characters across all embeds in one message;
  - on HTTP 429, wait `retry_after` seconds.
- Schedule: `0 0 9-21/3 * * *` in time zone `America/Los_Angeles`. It ships with `enabled: false`; enabling needs the user's OK.
- Production steps need the user's explicit OK, each one separately: applying the schema, running the backfill, enabling the schedule, changing private config, and stopping the mario stack.
- Text that people read (docs, the PR, Discord copy) follows the refactoringenglish.com style with no AI writing habits.
- Run tests from the main checkout's directory, with this worktree mounted:
  `cd /home/jonas/hitsave-e2e/hitsave-archiver && docker compose run --rm --no-deps -T --entrypoint "" -v "/home/jonas/hitsave-e2e/wt-discord-digest:/repo:ro" -w /repo -e PYTHONPATH=/repo/scripts -e CI=true ingest-worker sh -c "pip install -q pytest; python -m pytest <tests> -q -p no:cacheprovider"`
- Stage explicit paths only (no `commit -a`). Never use bare `git stash`.

## Review Focus

1. **Backfill before the first run.** If the first real run sees all of the ~2,437 already-public items as unannounced, it floods the channel. `--backfill` must mark them announced without posting, and the job must refuse to post when the backfill marker is missing. Test: `test_refuses_without_backfill`.
2. **Partial failure halfway through a run.** If message 2 of 4 fails, items in message 1 stay announced, items in messages 2–4 stay pending, and the job exits non-zero. Test: `test_failed_chunk_leaves_rest_pending`.
3. **Item hidden again or deleted before the digest.** If Omeka says `o:is_public` is false, the item is skipped and stays pending. On 404 it is skipped and marked `announced_at` with `skip_reason='deleted'`, so it isn't retried forever. Test: `test_hidden_and_deleted_items`.
4. **Titles or descriptions that are too long, empty, or contain Discord markdown or `@everyone`.** The text is truncated to the limits and sent with `allowed_mentions: {"parse": []}`, so nobody gets pinged. Test: `test_embed_limits_and_no_mentions`.
5. **Two runs at once,** for example a manual run during a scheduled one. A Postgres advisory lock makes the second run exit 0 with "another digest run holds the lock". Test: `test_second_run_skips_on_lock` (lock function injected).

---

## File Structure

| File | Responsibility |
|---|---|
| `schema/015_publication.sql` | `publication` table and `digest_state` key/value table |
| `scripts/apply-ledger-migrations.sh` | add `014_restore.sql` (missing from the list today) and `015_publication.sql` |
| `ledger/publication.py` | `record_published()`: insert one row, never raise into the request handler |
| `ledger/app.py` | call `record_published()` after a successful publish in `/review/publish` and `/visibility` |
| `scripts/discord_digest.py` | the digest job: backfill, reconcile, pick, recheck in Omeka, build embeds, post as the bot, mark |
| `windmill_ops/wmill/f/hitsave/discord_digest.{sh,script.yaml,schedule.yaml}` | the Windmill job and schedule (disabled) |
| `ingest-worker/Dockerfile` | add `scripts/discord_digest.py` to the explicit COPY list (the `omeka-catalog` image is built from it) |
| `docs/discord-digest.md` | what it posts, when, how to run it by hand, the backfill, how to pause it |
| `tests/test_publication_schema.py`, `tests/test_ledger_publication.py`, `tests/test_discord_digest.py` | tests |

---

### Task 1: Ledger records publication

**Files:**
- Create: `schema/015_publication.sql`, `ledger/publication.py`
- Modify: `scripts/apply-ledger-migrations.sh` (the `for f in …` list), `ledger/app.py` (the `/review/publish` handler around line 777 and the `/visibility` handler around line 807)
- Test: `tests/test_publication_schema.py`, `tests/test_ledger_publication.py`

**Interfaces:**
- Produces:
  - table `publication`:
    - `id BIGSERIAL PRIMARY KEY`
    - `omeka_item_id INTEGER NOT NULL UNIQUE`
    - `game_ingest_id BIGINT REFERENCES game_ingest (id) ON DELETE SET NULL`
    - `published_at TIMESTAMPTZ NOT NULL DEFAULT now()`
    - `published_by TEXT NOT NULL`
    - `source TEXT NOT NULL CHECK (source IN ('ledger','reconcile','backfill'))`
    - `announced_at TIMESTAMPTZ`
    - `discord_message_id TEXT`
    - `skip_reason TEXT`
  - index `publication_pending ON publication (published_at) WHERE announced_at IS NULL`;
  - table `digest_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`;
  - `record_published(connect: Callable[[], Connection], omeka_item_id: int, user: str, source: str = "ledger") -> bool`. It returns True when a new row was inserted and False when the row already existed. On any database error it prints one line and returns False.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_publication_schema.py
def test_publication_schema():
    sql = (ROOT / "schema" / "015_publication.sql").read_text()
    assert "omeka_item_id INTEGER NOT NULL UNIQUE" in sql
    for s in ("'ledger'", "'reconcile'", "'backfill'"):
        assert s in sql
    assert "WHERE announced_at IS NULL" in sql
    assert "CREATE TABLE IF NOT EXISTS digest_state" in sql

def test_migration_list_includes_014_and_015():
    sh = (ROOT / "scripts" / "apply-ledger-migrations.sh").read_text()
    assert "schema/014_restore.sql" in sh and "schema/015_publication.sql" in sh
```

`tests/test_ledger_publication.py` uses a fake connection that records `execute(sql, params)` and returns a cursor whose `rowcount` the test sets.
- `test_inserts_once`: with rowcount 1 it returns True. The SQL contains `ON CONFLICT (omeka_item_id) DO NOTHING`, and the params include `(31336, "editor@example.org", "ledger")`. The `game_ingest_id` comes from a subselect on `game_ingest.omeka_item_id`.
- `test_repeat_is_noop`: with rowcount 0 it returns False.
- `test_db_error_returns_false`: when `connect()` raises `psycopg.OperationalError`, it returns False and doesn't raise.

- [ ] **Step 2: Run them.** Expected: FAIL (missing file and module).
- [ ] **Step 3: Write the code.**
  - Write `schema/015_publication.sql`, headed with a comment block that explains each column, in the style of `schema/014_restore.sql`. Add both files to `apply-ledger-migrations.sh`.
  - Write `ledger/publication.py` with one statement:
    `INSERT INTO publication (omeka_item_id, game_ingest_id, published_by, source) SELECT %s, (SELECT id FROM game_ingest WHERE omeka_item_id = %s ORDER BY id LIMIT 1), %s, %s ON CONFLICT (omeka_item_id) DO NOTHING`.
  - In `app.py`, after `HIDDEN.mark_public(item_id)` in both handlers, call `record_published(connect, item_id, user or "-")`. Only call it on `code == 0` and `is_public`.
- [ ] **Step 4: Run them, then the SQL parse check.** Expected: PASS. Then:

  ```bash
  docker run --rm -d --name pub-pg -e POSTGRES_PASSWORD=x postgres:16 && sleep 5
  cat schema/0*.sql | docker exec -i pub-pg psql -U postgres -v ON_ERROR_STOP=1 -q
  docker rm -f pub-pg
  ```

  Expected: no errors.
- [ ] **Step 5: Run the ledger tests** (`tests/test_ledger_*.py`). Expected: all pass.
- [ ] **Step 6: Commit** `schema/015_publication.sql scripts/apply-ledger-migrations.sh ledger/publication.py ledger/app.py tests/test_publication_schema.py tests/test_ledger_publication.py` with the message "Ledger: record when an item is first published".

### Task 2: Digest job (backfill, reconcile, post)

**Files:**
- Create: `scripts/discord_digest.py`, `tests/test_discord_digest.py`
- Modify: `ingest-worker/Dockerfile` (COPY list)

**Interfaces:**
- Consumes: `publication` and `digest_state` from Task 1.
- Produces these functions in `scripts/discord_digest.py`. Each one takes its I/O as arguments, so tests pass fakes.
  - `ITEM_URL = "https://archive.hitsave.org/ark:/78322/{id}"`
  - `PLACEHOLDER = "More information to be added at a later date."`
  - `build_embed(item: dict) -> dict | None`: returns None for a placeholder description. Otherwise it returns a dict with these fields:
    - `title`: `o:title`, at most 256 characters, with `Untitled` as the fallback;
    - `url`;
    - `description`: the first `dcterms:description` value, cut to 300 characters with `…`;
    - `thumbnail.url`: `thumbnail_display_urls.medium`, then `large`, then `small`, left out if there is none;
    - `footer.text`: the first `dcterms:format` value, if there is one;
    - `color`: `0x3B82F6`.
  - `chunk_messages(embeds: list[dict]) -> list[list[dict]]`: at most 10 embeds per message and at most 6000 characters per message. Count title, description and footer text.
  - `API = "https://discord.com/api/v10"`
  - `post_message(post: Callable, token: str, channel_id: str, embeds: list[dict], *, first: bool, sleep=time.sleep) -> str`. It posts to `f"{API}/channels/{channel_id}/messages"` with the headers `Authorization: Bot <token>` and `User-Agent: DiscordBot (https://hitsave.org, 1.0)`. The JSON body has these fields:
    - `content`: `"New in the archive"` when `first`, otherwise `""`;
    - `embeds`;
    - `allowed_mentions`: `{"parse": []}`.

    It returns the message `id` from the response. On 429 it sleeps `retry_after` and retries, up to 3 times. Any other non-2xx raises `DigestError(f"Discord returned HTTP {status}")`, so the token never appears.
  - `run(conn, omeka_get, post, discord: dict, *, execute: bool, max_items: int = 40, lock=try_advisory_lock) -> dict`, where `discord` is `{"token": …, "channel_id": …}`: returns a summary dict with the keys `pending`, `posted`, `skipped_hidden`, `skipped_deleted`, `skipped_placeholder`, `messages`, `reconciled`.
  - `backfill(conn, public_ids: set[int], *, execute: bool) -> int`
  - `reconcile(conn, public_ids: set[int]) -> int`: inserts `source='reconcile'` rows for public items that are in `game_ingest.omeka_item_id` and have no `publication` row, with `published_by='reconcile'`.
  - `main() -> int`: takes `--execute`, `--backfill` and `--max-items`.

**Behaviour that `run` pins (the order matters):**

1. Take the advisory lock with `pg_try_advisory_lock(hashtext('discord_digest'))`. If it's taken, print `another digest run holds the lock` and return `{"locked": True}`.
2. Without `digest_state` key `backfilled`, raise `DigestError("run --backfill first")`.
3. Reconcile against the set of public item ids. The set comes from the authenticated Omeka API with `is_public=1`, paged with `per_page=1000`.
4. Select pending rows (`announced_at IS NULL`), oldest `published_at` first, `LIMIT max_items`.
5. Fetch each item with credentials:
   - on 404, set `announced_at=now()` and `skip_reason='deleted'`;
   - if `o:is_public` is not True, skip the item and leave it pending;
   - for a placeholder description, skip it and leave it pending.
6. Post the messages in chunks. After each message that succeeds, in one transaction, set `announced_at=now()` and `discord_message_id` for exactly the items in that chunk.
7. If a post fails, stop. The items still unposted stay pending, and `main` exits 1.
8. When `execute` is false, steps 3–6 read only and print what would be posted. The reconcile inserts happen only in execute mode.

`--backfill` inserts every public ledger item without a row as `source='backfill'`, `published_by='backfill'`, `announced_at=now()`, `skip_reason='backfill'`. It then sets `digest_state('backfilled', <UTC time>)`. It runs only with `--execute`; without it, it only prints the count.

**Config in `main`:**
- Omeka base URL and credentials load like `scripts/sync-catalog-snapshots.py` lines 175–183, using `omeka-catalog.yaml` `api.base_url` and `credentials_file`.
- Requests send `User-Agent: HitSaveDigest/1.0`. The public API returns 403 without one.
- The ledger connection loads like the same file's lines 185–198.
- The token and channel ID come from `/config/secrets/discord-bot.yaml`. A missing file raises `DigestError("no Discord bot configured")` in execute mode only.

- [ ] **Step 1: Write the failing tests.** Use an in-memory fake `conn` that implements the SQL calls `run` makes through small helper methods, or use a real throwaway Postgres if the helpers end up thin. Decide at Step 3 and keep the tests the same.

```python
def test_build_embed_fields():
    item = {"o:id": 31336, "o:title": "Aka", "dcterms:description": [{"@value": "A game."}],
            "dcterms:format": [{"@value": "Nintendo Switch"}],
            "thumbnail_display_urls": {"medium": "https://x/m.jpg"}}
    e = dd.build_embed(item)
    assert e["url"] == "https://archive.hitsave.org/ark:/78322/31336"
    assert e["title"] == "Aka" and e["description"] == "A game."
    assert e["thumbnail"] == {"url": "https://x/m.jpg"} and e["footer"] == {"text": "Nintendo Switch"}

def test_placeholder_is_skipped():
    item = {"o:id": 1, "o:title": "X", "dcterms:description": [{"@value": dd.PLACEHOLDER + " Soon."}]}
    assert dd.build_embed(item) is None

def test_embed_limits_and_no_mentions():
    e = dd.build_embed({"o:id": 2, "o:title": "T" * 400, "dcterms:description": [{"@value": "@everyone " + "d" * 5000}]})
    assert len(e["title"]) == 256 and len(e["description"]) <= 300 and e["description"].endswith("…")
    sent = []
    dd.post_message(lambda url, json, headers, timeout: sent.append(json) or FakeResp(200, {"id": "9"}), "tok", "123", [e], first=True)
    assert sent[0]["allowed_mentions"] == {"parse": []} and sent[0]["content"] == "New in the archive"

def test_chunk_messages_respects_10_and_6000():
    embeds = [{"title": "t", "description": "d" * 1000} for _ in range(25)]
    chunks = dd.chunk_messages(embeds)
    assert all(len(c) <= 10 for c in chunks)
    assert all(sum(len(x["title"]) + len(x["description"]) for x in c) <= 6000 for c in chunks)
    assert sum(len(c) for c in chunks) == 25

def test_429_waits_and_retries():  # first response 429 retry_after 1.5, then 200; sleep called with 1.5

def test_token_never_in_error():  # 500 response: DigestError message is "Discord returned HTTP 500" and has no "tok"

def test_refuses_without_backfill():  # no digest_state 'backfilled' -> DigestError("run --backfill first")

def test_backfill_marks_public_items_announced():  # public {5,6}, ledger has 5,6,7 -> rows for 5,6 announced, 'backfilled' set

def test_reconcile_adds_only_ledger_items():  # public {5,99}, ledger has 5 only -> one reconcile row for 5

def test_hidden_and_deleted_items():  # item A 404 -> announced+deleted; item B is_public False -> still pending; nothing posted for either

def test_failed_chunk_leaves_rest_pending():  # 12 pending, post #1 ok, post #2 raises -> first 10 announced, last 2 pending, DigestError raised

def test_max_items_carries_over():  # 50 pending, max_items 40 -> 40 posted oldest first, 10 pending

def test_dry_run_writes_nothing():  # execute False -> no UPDATE/INSERT issued, summary counts filled

def test_second_run_skips_on_lock():  # lock returns False -> {"locked": True}, no queries after the lock
```

Write the stubbed tests out in full, with the assertions given in their comments.
- [ ] **Step 2: Run them.** Expected: FAIL (no module `discord_digest`).
- [ ] **Step 3: Write `scripts/discord_digest.py`** to the interfaces above. Add it to the COPY list in `ingest-worker/Dockerfile`.
- [ ] **Step 4: Run `tests/test_discord_digest.py`, then the whole suite.** Expected: all pass.
- [ ] **Step 5: Commit** `scripts/discord_digest.py tests/test_discord_digest.py ingest-worker/Dockerfile` with the message "Discord digest: post newly published items to the changelog channel".

### Task 3: Windmill job, schedule and docs

**Files:**
- Create: `windmill_ops/wmill/f/hitsave/discord_digest.sh`, `discord_digest.script.yaml`, `discord_digest.schedule.yaml`, `docs/discord-digest.md`
- Modify: `windmill_ops/README.md` (list the job, if the README lists jobs), `docs/README.md` or the docs index (if there is one)

**Interfaces:**
- Consumes: `main()` arguments from Task 2.
- Produces: Windmill path `f/hitsave/discord_digest`, with string args `execute` (default `'true'`) and `max_items` (default `'40'`).

- [ ] **Step 1: Write the files.**
  - `discord_digest.sh`: copy the shape of `sync_catalog_snapshots.sh`. Source `/run/hitsave/hitsave-bootstrap.sh`, then run `archiver_compose run --rm -T --no-deps -v "$ARCHIVER_HOST_PATH/scripts:/app/scripts:ro" --entrypoint python omeka-catalog /app/scripts/discord_digest.py --max-items "$max_items"`, adding `--execute` unless `execute` is `false`.
  - `discord_digest.script.yaml`: same keys as `sync_catalog_snapshots.script.yaml`, with summary "Post newly published items to Discord".
  - `discord_digest.schedule.yaml`: `schedule: 0 0 9-21/3 * * *`, `timezone: America/Los_Angeles`, `enabled: false`, `script_path: f/hitsave/discord_digest`, `args: {execute: 'true', max_items: '40'}`. Other keys as in `sync_catalog_snapshots.schedule.yaml`.
  - `docs/discord-digest.md` covers:
    - what is posted and when (5 runs a day, items published overnight go out at 09:00);
    - the once-per-item rule;
    - the placeholder rule;
    - the 40-item cap, with the rest posted at the next run;
    - the backfill;
    - a manual dry run;
    - how to pause it (disable the schedule);
    - where the bot token lives (private config, by path only).
- [ ] **Step 2: Validate the schedule on the target before the PR** (memory `validate-config-on-target`). Push the script and the disabled schedule to Windmill with the CLI recipe in memory `hitsave-deploy-checklist`. Then read it back with `GET /api/w/hitsave-archiver/schedules/get/f/hitsave/discord_digest` and confirm two things: `enabled` is false, and the next five run times Windmill reports fall at 09/12/15/18/21 Pacific. Nothing runs, because the schedule is disabled.
- [ ] **Step 3: Run `bash -n` on the `.sh` file, then the whole test suite.** Expected: no syntax errors, all tests pass.
- [ ] **Step 4: Commit** the four new files and any index edits with the message "Windmill: Discord digest job, disabled until backfilled".

### Task 4: PR, then deploy and retire the old bot (each production step needs the user's OK)

- [ ] **Step 1: Open the PR.** Push `discord-digest` and open a PR whose body follows the writing style. Merge when CI is green. The `hitsave-deploy` container then rebuilds the images, pushes Windmill, and redeploys the ledger on luigi.
- [ ] **Step 2 (OK needed): Apply the schema.** Before the ledger deploy uses it, apply `schema/015_publication.sql` to the production ledger on luigi. Also check that `014` is applied, since it was missing from the migration list.
- [ ] **Step 3: Nothing to create in Discord.** The old bot account is reused.
- [ ] **Step 4 (OK needed):** Copy `DISCORD_TOKEN` and `DISCORD_CHANNEL_ID` from mario's `/etc/komodo/repos/docker-inventory/mario/omeka-discord-bot/.env` into `hitsave-archiver-config/secrets/discord-bot.yaml` (`token`, `channel_id`, mode 600). Copy over SSH without printing the values, and commit the file in the private config repo.
- [ ] **Step 5 (OK needed):** Run a backfill dry run, then `--backfill --execute` once. Expected: about 2,437 rows marked `backfill`, and `digest_state.backfilled` set.
- [ ] **Step 6:** Run a manual dry run with `execute=false` in Windmill. Expected: `pending` equals the items published since the backfill, nothing posted.
- [ ] **Step 7 (OK needed):** Publish one item through the ledger, run the job once with `execute=true`, and check the post in Discord. Then enable the schedule.
- [ ] **Step 8 (OK needed): Retire the old bot.**
  - On mario, `docker compose -p omeka-discord-bot down` in `/etc/komodo/repos/docker-inventory/mario/omeka-discord-bot`.
  - Open a docker-inventory PR that removes the `mario/omeka-discord-bot` stack, and remove it from Komodo.
  - Archive the `hitsave/omeka-discord-bot` repo. Keep the bot account and token, because the digest uses them now.
  - Remove the `.env` from the docker-inventory checkout on mario once the stack is gone.
- [ ] **Step 9:** Update memory `discord-bot-mario` to say the bot is replaced by the digest, and give the job path.

## Rulings made while planning (for the user to confirm)

- **Not crossposted.** #the-archive-changelog is an announcement channel. Like the old bot, the digest doesn't crosspost to servers that follow the channel. Crossposting could be added later.
- **Time zone: Pacific** (the old bot's `TZ` and the mario backup schedules use it). Runs at 09, 12, 15, 18 and 21. If it's wrong, it's a one-line schedule change.
- **Only ledger items are announced.** Items made public in Omeka with no ledger package (hand-made records) are not posted. The old bot posted any public item with media.
- **At most 40 items per run (4 messages).** The rest are posted at the next run, oldest first, so a large publishing session spreads over several runs. A batch of 200 takes 5 runs, about one day.
- **Publication is recorded in the ledger handlers, not in `set-omeka-visibility.py`.** Publishing from the CLI or the Omeka admin is caught by the reconcile step at the next run, dated at the time it was detected.
