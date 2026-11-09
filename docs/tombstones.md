# Duplicate records and tombstones

An ARK on archive.hitsave.org is the Omeka item id, so it can never move to another item, and a
public item is never deleted: Omeka can't recreate an item with a chosen id.

When two records describe the same thing:

1. Keep the older public record and its ARK. Merge the newer record's data into it with a
   full-item update, move its package media, and point its ledger row at the older record.
2. Turn the newer record into a tombstone:
   - keep it public, with its own ARK;
   - replace its values with a title, a short description of what happened, and
     `dcterms:isReplacedBy` linking to the older record;
   - remove it from all item sets;
   - remove it from the site.

A tombstone's ARK still opens its page, which links to the record that replaced it. Because it
isn't assigned to the site, it doesn't appear in browse, search or faceted browse.

`scripts/hide-tombstones.py` takes every item with `dcterms:isReplacedBy` off the site. It's a dry
run by default; with `--execute` it backs up each item, updates it, and checks that only its site
changed.

```sh
docker compose run --rm -T --no-deps -e HITSAVE_CONFIG_ROOT=/config \
  -v "$PWD/scripts:/app/scripts:ro" -v "$PWD/.generated/tombstones:/out" \
  -v "${HITSAVE_PRIVATE_CONFIG}/omeka-catalog.yaml:/config/omeka-catalog.yaml:ro" \
  --entrypoint python omeka-catalog /app/scripts/hide-tombstones.py --out /out [--execute]
```

To undo it for one item, put the `o:site` value from its backup back with a full-item update.
