# Disc contents of data-disc dumps

PC, Mac and other computer discs (press kits, bonus discs) hold videos, pictures and documents.
The dump's bag keeps the disc image as the preservation copy and is never changed. The disc's
files go into a second BagIt package, the **contents bag**, which the Omeka item shows like press
material: images, videos (MP4 web copies), documents (PDF web copies) and the file tree.

## Which discs

`scripts/disc_contents_policy.py`, from the Redump sidecar and the contributor's folder name:

| Disc | Display |
|---|---|
| Labelled press kit (Edition, Comments or folder name: press kit / press disc / EPK / media kit) | `public` |
| Retailer disc (Redump Title: "Retailer DVD", "Retail Disc", "Trade Kit"…; never the Edition, where "Retail" means the shop release) | `review` |
| Multimedia, Video, Audio, Bonus Discs, Coverdiscs, or unknown category | `review` (staff decide in the ledger) |
| Games, Demos, Applications, Educational, Add-Ons, Preproduction | `none`, even with a press kit label |
| Console and video discs | `skip` |

Labelled press kits and retailer discs are catalogued in the press material item set
(`dump_catalog.press_kit` in omeka-catalog.yaml, `is_press_material` in dump_catalog_omeka.py).

The decision is on the ledger row (`game_ingest.disc_contents_*`, schema/012). Staff decisions
(review → public / none) stand when the step runs again.

## Where it's stored

Same bucket as the dump, `disc-contents/` in place of `dumps/`:

```
s3://hitsave-dumps/dumps/Redump/IBM PC compatible/Multimedia/T/<package_id>/          disc image bag
s3://hitsave-dumps/disc-contents/Redump/IBM PC compatible/Multimedia/T/<package_id>/  contents bag
```

So the contents stay hot when an idle dump's image moves to cold storage, and tools that work on
a dump's prefix (fixity, renames, validation) never see the contents bag.

Contents bag layout:

- `data/<volume label>/…`: the disc's files, original names and dates
- `metadata/inventory.json`: per file path, size, sha256, mime, kind, `download` (false for
  programs: listed, not downloadable) and `access` (web copy: path, mime, size)
- `metadata/hitsave/access/<key>.mp4|.pdf|.jpg`: web copies, keyed like the viewer (`f<N>`)
- `metadata/hitsave/preview-manifest.json`: grid/display previews (contact sheet per video, first
  page per document), `video_file_keys`, `document_file_keys`
- `metadata/submission.json`: source dump, classification, extraction tools, ClamAV summary

## Steps

Run by the remote-intake sweep after Omeka registration (`remote_intake_disc_contents` in
`scripts/remote-intake/common.sh`); each can be rerun by hand:

1. `build_disc_contents_bag.py classify --slug S` (ingest-worker): decision → ledger. Exit 3 = not shown.
2. `docker compose run --rm disc-contents --package /data/intake/ready/S --out /output/S`
   (`disc-contents/Dockerfile`, no network): files + web copies under `<output>/disc-contents/S/`.
   Raw CD sectors (BIN MODE1/MODE2 2352) are read through a scratch ISO. There is no size cap:
   a full DVD-ROM needs its size in free space under `<output>/disc-contents/` (files + scratch).
   When a folder holds the same clip in several encodes (`_BIG` / `_HD_720p` / `_HI` / `_LO`…),
   only the best one (resolution label, then size) gets an MP4; the others stay downloadable.
3. `build_disc_contents_bag.py build --slug S` (ingest-worker): ClamAV over every file (malware →
   display `none`), build and validate the bag, verified upload, ledger prefix and summary.
4. `build_disc_contents_bag.py attach --slug S` (omeka-catalog): Omeka media on the item, public
   only when the display is `public` and the item is public; reruns refresh `previews_version`.
