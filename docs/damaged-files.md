# Damaged files

Hit Save preserves every file as received, including damaged ones. A damaged file is recorded
as damaged so that it can be replaced if someone has an intact copy.

## What is checked

`scripts/file_integrity.py` checks four kinds of file, chosen by file extension:

| Kind | Tool | A file is damaged when |
|------|------|------------------------|
| TIFF | `tiffinfo -D` (libtiff) | libtiff can't read or decode a strip or tile on any page |
| Other images (JPEG, PNG, GIF, BMP, WebP, TGA, PCX) | Pillow | It doesn't open, a PNG chunk checksum fails, or the image data ends early |
| PDF | `qpdf --check` | qpdf reports errors, or warnings that mean content is lost (no trailer, a stream it can't recover) |
| Video and audio | `ffprobe`, then `ffmpeg` decoding the last 15 seconds | ffprobe finds no duration, or the end of the file doesn't decode |
| Archives (ZIP, 7z, RAR) | `7zz t` | 7-Zip reports an error |

TIFFs go to libtiff, the reference TIFF library, because Pillow can't open some valid TIFFs
(CMYK with an alpha channel, common in print-ready art) and doesn't notice every broken
compressed strip. libtiff reads one strip at a time, so large scans have no size limit. When
Pillow can't identify any other image, a full `ffmpeg` decode decides.

Other files, such as disc images, text and programs, aren't checked. Dumps are verified against
their Redump or No-Intro hashes instead.

Each checked file gets one of three statuses:

- `ok`: the file opened and decoded without errors. A PDF that qpdf repaired without losing
  content is `ok`, with `"detail": "recoverable: …"`.
- `damaged`: the tool reported the file as cut off, corrupt, or not the format its name says.
- `unchecked`: the check couldn't answer, for example because the file has a password, a non-TIFF
  image is over 500 megapixels, or the tool timed out.

A check never stops a package from being preserved.

## Where results are recorded

- `metadata/inventory.json`: each checked file has an `integrity` record (`status`, `check`,
  `detail`), and `summary.integrity` counts the statuses.
- The ledger's `inventory_json` holds the same records.
- `metadata/premis/validation-*.xml`: a PREMIS `validation` event. Its outcome is `pass`, or
  `fail` with the damaged files listed.

## New packages

`build-bagit-package.py` checks every payload file before the bag is made, and prints each
damaged file in the sweep log.

## Packages preserved before the check existed

`scripts/check-integrity-wasabi.py` checks packages on Wasabi. Images, PDFs and archives are
downloaded, and their bytes must match the bag's `manifest-sha256.txt`. Video and audio are read
from a presigned URL, so ffmpeg fetches only the parts it needs. The script refuses to run if
qpdf, ffprobe, ffmpeg, tiffinfo or 7-Zip is missing. `--recheck-unchecked` checks files recorded as
`unchecked` again. Disc-contents bags have no ledger row, so this script doesn't cover them yet.

```sh
docker compose run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" \
  --entrypoint python ingest-worker /app/scripts/check-integrity-wasabi.py \
  [--execute] [--game-key KEY ...] [--kind press_material] [--limit N] [--max-gb 50] [--report out.json]
```

Without `--execute`, the script checks files and prints results but writes nothing. With
`--execute`, it updates `metadata/inventory.json` on Wasabi, adds the validation event, uploads
the tagmanifest last, and updates the ledger. It never changes the payload, `manifest-sha256.txt`
or `bag-info.txt`.

## The public page

`scripts/publish-damaged-files-page.py` builds the "Damaged files" page on the archive
(`/s/start/page/damaged-files`). The page lists each damaged file of a public item with its
name, folder, type, size and problem, and asks visitors with an intact copy to get in touch.
A file is listed only if the public Omeka API, called without a key, returns its item.

```sh
docker compose run --rm -T --no-deps -e HITSAVE_CONFIG_ROOT=/config \
  -v "$PWD/scripts:/app/scripts:ro" -v "$PWD/.generated/damaged-files:/out" \
  --entrypoint python omeka-catalog /app/scripts/publish-damaged-files-page.py --out /out [--execute]
```

Without `--execute`, the script writes `damaged-files.html` to `--out` for review. With
`--execute`, it creates the page, or replaces the page's blocks after saving the old page as JSON
in `--out`. Adding the page to the site menu is a one-time step in the Omeka admin.

## Replacing a damaged file

If someone sends an intact copy, check that it is the same file: the same name and content, and
usually a larger size than the cut-off copy. Add the intact copy to the package and keep the
damaged copy in the payload, because it is what we received.
