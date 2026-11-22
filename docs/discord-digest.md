# Discord digest

The digest posts newly published archive items to the **#the-archive-changelog** channel on the
HitSave Discord server. It posts as the archive's bot account ("Hit Save Archive Announcer").
It replaces the old `omeka-discord-bot` on mario, which looked for items by their Omeka creation
date. Items are created private and published later, so that bot never saw them.

## What gets posted, and when

`scripts/discord_digest.py` runs as the Windmill job `f/hitsave/discord_digest` at 09:00,
12:00, 15:00, 18:00 and 21:00 Pacific time. It doesn't run overnight, so items published after
21:00 go out at 09:00 the next morning.

Each run:

1. Records items that were published without the ledger seeing it: ledger items that are
   public in Omeka but have no publication row yet. This covers items published by a script or
   in the Omeka admin.
2. Takes up to 40 unannounced items, oldest first. The rest go out at the next run, so a large
   publishing session is shared over several posts. Items that a run skips (see the next step)
   move behind newer ones, and a run reads at most 200 items, so skipped items can't hold up the
   queue.
3. Reads each item from Omeka again:
   - A deleted item is closed without a post.
   - An item that was hidden again waits until it is public again.
   - An item whose description still starts with "More information to be added at a later
     date." waits until the description is written.
4. Posts one message for every 10 items. Each item shows its title, the start of its
   description, a thumbnail, its format and the ARK link. The thumbnail is the item's Omeka
   thumbnail if it has one. Most archive items don't (their media is a package, and Omeka gives
   them a blank placeholder), so the digest uses the same preview the archive site shows in item
   lists (`/omeka-dip/browse-preview/<media id>`), and leaves the thumbnail out when there is none. Nobody is mentioned, even if a
   description contains `@everyone`.
5. Marks each message's items as announced as soon as Discord accepts that message. If a later
   message fails, its items stay unannounced and go out at the next run.

Each item is announced once. Hiding an item and publishing it again doesn't post it again.

## Where the record lives

The ledger table `publication` (`schema/015_publication.sql`) has one row per published Omeka
item:
- `source` is `ledger` when the item was published from the review queue or the item page,
  `reconcile` when the job found it, and `backfill` for items that were public before the
  digest started.
- `announced_at` and `discord_message_id` show when and in which message it was posted.
- `skip_reason` says why an item was closed without a post.

The bot token and channel ID are in the private config repo, in
`secrets/discord-bot.yaml` (`token`, `channel_id`).

## Running it by hand

Dry run, which posts nothing and writes nothing: run `f/hitsave/discord_digest` in Windmill with
`execute: 'false'`, or:

```bash
source ../hitsave-archiver-config/host.env
docker compose run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" \
  --entrypoint python omeka-catalog /app/scripts/discord_digest.py
```

**Backfill, once, before the first real run.** It marks every item that is already public as
announced without posting, including items with no ledger package, so the first run doesn't post the whole archive. The job refuses to
post until the backfill has run.

```bash
docker compose run ... omeka-catalog /app/scripts/discord_digest.py --backfill            # count only
docker compose run ... omeka-catalog /app/scripts/discord_digest.py --backfill --execute
```

Only one run at a time: a second run while one is active exits without doing anything.

Delivery is at least once. If Discord accepts a message but the reply is lost, or the ledger
can't be updated right after a post, those items are posted again at the next run.

## Pausing it

Disable the `f/hitsave/discord_digest` schedule in Windmill. Publications keep being recorded,
and the next run after you enable it again posts them, 40 at a time.
