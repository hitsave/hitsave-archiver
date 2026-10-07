# Submission security test archives

Safe, **non-malware** samples for verifying portable zip intake. Run only against the preservation stack (container isolation); do not unpack `42.zip`-class bombs on production hosts.

**Runner:** `./scripts/test-submission-security-fixtures.sh`

## Downloaded samples (third party)

| File | Source | Expected result |
|------|--------|-----------------|
| `eicar_com.zip` | [EICAR](https://secure.eicar.org/eicar_com.zip) — industry-standard AV test string in a zip | **Reject** — ClamAV `FOUND` on zip scan |
| `eicarcom2.zip` | Built locally: outer zip wrapping `eicar_com.zip` | **Reject** — ClamAV and/or `reject_nested_archives` (`.zip` member) |
| `zip-slip.zip` | [Snyk zip-slip sample](https://github.com/snyk/zip-slip-vulnerability/tree/master/archives) | **Reject** — zip slip / unsafe path during `inspect_zip` |
| `zip-slip-traversal.zip` | [HEXAQA](https://hexaqa.com/file/archives/zip-slip-traversal-zip) (SHA-256 `bf5ee2334900a786100f6b030fa929e1a6101b1075f501ff8d7be41c0b38107e`) | **Reject** — path traversal |

Re-download:

```bash
curl -fsSL -o fixtures/submissions-security/eicar_com.zip https://secure.eicar.org/eicar_com.zip
curl -fsSL -o fixtures/submissions-security/zip-slip.zip \
  https://raw.githubusercontent.com/snyk/zip-slip-vulnerability/master/archives/zip-slip.zip
curl -fsSL -o fixtures/submissions-security/zip-slip-traversal.zip \
  https://files.hexaqa.com/archive/zip-slip-traversal.zip
```

## Generated samples (`scripts/generate-submission-security-fixtures.py`)

| File | Simulates | Expected result |
|------|-----------|-----------------|
| `valid-minimal.zip` | Good archivist package (`.jpg` + `.txt`) | **Accept** with `--skip-ingest` |
| `allowlist-exe.zip` | `.exe` in tree | **Reject** — extension allowlist |
| `nested-inner-zip.zip` | `.zip` inside submission | **Reject** — nested archive policy |
| `extract-bytes-cap.zip` | Payload &gt; 4 KiB | **Reject** — `max_extracted_bytes` during streamed extract (uses `ci-tiny-limits.yaml` in the test script) |

Regenerate synthetics:

```bash
python3 scripts/generate-submission-security-fixtures.py
```

## Zip bomb samples (downloaded by test script)

Fetched by [`scripts/fetch-submission-security-bombs.sh`](../../scripts/fetch-submission-security-bombs.sh) from [ShiftLeftSecurity/zipdu `bombs/`](https://github.com/ShiftLeftSecurity/zipdu):

| File | Compressed | Expands to (if fully extracted) | Observed rejection |
|------|------------|----------------------------------|--------------------|
| `bombs/42.zip` | ~42 KiB | ~4.5 PB (nested zip layers) | **Nested archive policy** — members like `lib 0.zip` |
| `bombs/philkatz.zip` | ~971 KiB | ~1 GiB | **Special file type** — archive entry named `-` |

**Note:** With `reject_nested_archives: false`, `42.zip` still passes **central-directory** size checks (~558 KiB declared) because the classic bomb only detonates on recursive extract. Blocking nested `.zip` in submissions is intentional and is what stops `42.zip` here. Lying metadata is covered by `metadata-oversize.zip` in the generated fixtures.

Do **not** fully extract these on a production host outside the ingest container.

## Optional external samples (manual)

| Sample | Source | Expected if you test manually |
|--------|--------|--------------------------------|
| AMTSO compressed EICAR | [AMTSO feature checks](https://www.amtso.org/feature-settings-check-download-of-compressed-malware/) | **Reject** — ClamAV |
| Symlink / link-slip corpus | [MegaManSec/zip-slip-tar-slip-generator](https://github.com/MegaManSec/zip-slip-tar-slip-generator) | **Reject** — symlink entries / post-extract symlink walk |

## EICAR note

EICAR files are **not viruses**; scanners treat them as detections by convention ([EICAR](https://www.eicar.org/)). Git and desktop AV may flag these zips — that is expected.
