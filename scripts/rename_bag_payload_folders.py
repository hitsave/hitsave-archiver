#!/usr/bin/env python3
"""Give remote-intake bags on Wasabi their contributor's original folder name.

Bags built before original folder names (2026-10-13 .. this change) hold their payload under
intake's staging name, data/<ready slug>/, with intake's own game.ingest.yaml and
hitsave-submission.yaml inside the payload. For each such bag this:

  1. MOVEs (Wasabi server-side rename, verified) data/<slug>/game.ingest.yaml and
     hitsave-submission.yaml to metadata/intake/, then data/<slug>/ to data/<original name>/.
     The original name is the package_folder_name / drive_folder_name intake recorded in the
     bag's own hitsave-submission.yaml.
  2. Rewrites the tag files to match: manifest-sha256.txt, bag-info.txt (Payload-Oxum),
     metadata/inventory.json, metadata/submission.json, metadata/hitsave/preview-manifest.json
     (preview keys are f<N> by position in the payload listing, so they're renumbered), and
     tagmanifest-sha256.txt last. Payload bytes are never rewritten: digests carry over.
  Before touching a bag, its tag files and the plan are saved under
  /output/rename-bag-payload-folders-backup/<game_key>/.
  3. Updates the ledger (inventory_json, file_count, total_bytes) and bumps the Omeka media's
     previews_version so omeka-dip-viewer rebuilds its cached file index.
  4. Renames the local working copy under /output/bag the same way, if there still is one.

Idempotent and resumable: each MOVE is skipped when it already happened (source gone,
destination present), and tag files are rebuilt from the bag's own manifest. A bag whose
payload is already under its original name is left alone.

Usage (in the ingest-worker container; scripts/remote-intake/rename-bag-payload-folders.sh):
  rename_bag_payload_folders.py [--execute] [--limit N] (--all | GAME_KEY ...)
Without --execute: prints the plan per bag, changes nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from preservation_common import (  # noqa: E402
    GAME_INGEST_FILENAME,
    INTAKE_METADATA_SUBDIR,
    original_folder_name,
    safe_folder_name,
)

HANDOFF_FILENAME = "hitsave-submission.yaml"
INTAKE_FILES = (GAME_INGEST_FILENAME, HANDOFF_FILENAME)
PREVIEW_MANIFEST = "metadata/hitsave/preview-manifest.json"
INVENTORY = "metadata/inventory.json"
SUBMISSION = "metadata/submission.json"
BAG_ROOT = Path("/output/bag")
BACKUP_ROOT = Path("/output/rename-bag-payload-folders-backup")


class RenameError(RuntimeError):
    pass


# --- BagIt manifest lines -----------------------------------------------------------------------

def encode_manifest_path(path: str) -> str:
    """CR and LF percent-encoded in manifest paths, exactly as bagit-python (which builds and
    validates our bags) writes them; it leaves '%' as is."""
    return path.replace("\r", "%0D").replace("\n", "%0A")


def decode_manifest_path(path: str) -> str:
    return path.replace("%0A", "\n").replace("%0a", "\n").replace("%0D", "\r").replace("%0d", "\r")


def parse_manifest(text: str) -> list[tuple[str, str]]:
    """[(digest, decoded path)] in file order."""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        digest, path = line.split(None, 1)
        out.append((digest, decode_manifest_path(path.strip())))
    return out


def format_manifest(entries: list[tuple[str, str]]) -> str:
    return "".join(f"{d}  {encode_manifest_path(p)}\n" for d, p in entries)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- the plan for one bag -----------------------------------------------------------------------

@dataclass
class BagPlan:
    old_folder: str
    new_folder: str
    # (source relative key, destination relative key) object MOVEs, in order
    intake_moves: list[tuple[str, str]] = field(default_factory=list)
    tag_files: dict[str, bytes] = field(default_factory=dict)  # relative key -> new content
    payload_count: int = 0
    payload_bytes: int = 0
    inventory: dict | None = None  # new metadata/inventory.json (also the ledger's inventory_json)

    @property
    def folder_move(self) -> tuple[str, str]:
        return f"data/{self.old_folder}", f"data/{self.new_folder}"


def payload_folder(manifest: list[tuple[str, str]]) -> str:
    tops = {PurePosixPath(p).parts[1] for _d, p in manifest if p.startswith("data/") and p.count("/") >= 2}
    if len(tops) != 1:
        raise RenameError(f"expected one payload folder under data/, found {sorted(tops)}")
    return tops.pop()


def renamed(path: str, old: str, new: str) -> str:
    prefix = f"data/{old}/"
    if not path.startswith(prefix):
        raise RenameError(f"payload path outside data/{old}/: {path}")
    return f"data/{new}/" + path[len(prefix):]


def viewer_keys(paths: list[str]) -> dict[str, str]:
    """data/... path -> f<N>, the key omeka-dip-viewer gives it (byte-order sort of the paths)."""
    rel = sorted((p[len("data/"):] for p in paths), key=lambda s: s.encode("utf-8"))
    return {f"data/{r}": f"f{i}" for i, r in enumerate(rel)}


def rekey_preview_manifest(manifest: dict, old_paths: list[str], old: str, new: str, kept: set[str]) -> dict:
    """Rename payload paths and renumber f<N> keys to the viewer's keys for the new payload.

    The old key of a file comes from the manifest's own files map when it has one, else from the
    viewer's numbering of the old payload. Keys that aren't f<N> (the DVD "menu" still) stay.
    """
    old_keys = viewer_keys(old_paths)
    key_to_old_path = {k: p for p, k in old_keys.items()}
    for key, rec in (manifest.get("files") or {}).items():
        if isinstance(rec, dict) and rec.get("path"):
            key_to_old_path[key] = str(rec["path"])
    new_keys = viewer_keys([renamed(p, old, new) for p in old_paths if p in kept])

    def new_key(key: str) -> str | None:
        if not re.fullmatch(r"f\d+", key):
            return key
        path = key_to_old_path.get(key)
        if path is None or path not in kept:
            return None  # previewed file isn't payload any more (can't happen for images)
        return new_keys[renamed(path, old, new)]

    out = dict(manifest)
    for section in ("grid", "display", "files"):
        if isinstance(manifest.get(section), dict):
            sec = {}
            for key, value in manifest[section].items():
                nk = new_key(key)
                if nk is None:
                    continue
                if section == "files" and isinstance(value, dict) and value.get("path"):
                    value = {**value, "path": renamed(str(value["path"]), old, new)}
                sec[nk] = value
            out[section] = sec
    if isinstance(manifest.get("gallery_file_keys"), list):
        out["gallery_file_keys"] = [nk for k in manifest["gallery_file_keys"] if (nk := new_key(str(k)))]
    return out


def rename_inventory(inventory: dict, old: str, new: str, drop: set[str]) -> dict:
    files = []
    ext_counts: Counter[str] = Counter()
    total = 0
    for rec in inventory.get("files") or []:
        path = str(rec.get("path") or "")
        if path in drop:
            continue
        rec = {**rec, "path": renamed(path, old, new)}
        files.append(rec)
        ext_counts[PurePosixPath(path).suffix.lower().lstrip(".") or "(none)"] += 1
        total += int(rec.get("size_bytes") or 0)
    out = {**inventory, "files": files}
    out["summary"] = {
        **(inventory.get("summary") or {}),
        "file_count": len(files),
        "total_bytes": total,
        "by_extension": dict(sorted(ext_counts.items())),
    }
    return out


def set_payload_oxum(bag_info: str, total_bytes: int, count: int) -> str:
    line = f"Payload-Oxum: {total_bytes}.{count}"
    if re.search(r"^Payload-Oxum:", bag_info, flags=re.M):
        return re.sub(r"^Payload-Oxum:.*$", line, bag_info, count=1, flags=re.M)
    return bag_info.rstrip("\n") + "\n" + line + "\n"


def name_from_intake_source(intake_source: str | None) -> str | None:
    """Folder name of a package imported from a local path (ledger intake_source 'local:/a/b/Name')."""
    src = str(intake_source or "")
    if src.startswith("local:"):
        name = PurePosixPath(src[len("local:"):].rstrip("/")).name
        return name or None
    return None


def plan_bag(tag: dict[str, bytes], handoff: bytes, *, fallback_name: str | None = None) -> BagPlan | None:
    """The rename for one bag from its current tag files, or None when it needs none.

    ``tag`` holds manifest-sha256.txt, tagmanifest-sha256.txt, bag-info.txt and the metadata/
    JSON files (preview manifest optional); ``handoff`` is the bag's hitsave-submission.yaml.
    """
    manifest = parse_manifest(tag["manifest-sha256.txt"].decode("utf-8"))
    old = payload_folder(manifest)
    data = yaml.safe_load(handoff) or {}
    new = (original_folder_name(data) if isinstance(data, dict) else None) or fallback_name
    if not new:
        raise RenameError("hitsave-submission.yaml records no original folder name")
    if not safe_folder_name(new):
        raise RenameError(f"original folder name {new!r} can't be a bag folder")
    if new == old:
        return None

    plan = BagPlan(old_folder=old, new_folder=new)
    digests = {p: d for d, p in manifest}
    intake_paths = {f"data/{old}/{name}" for name in INTAKE_FILES if f"data/{old}/{name}" in digests}
    plan.intake_moves = [
        (p, f"metadata/{INTAKE_METADATA_SUBDIR}/{PurePosixPath(p).name}") for p in sorted(intake_paths)
    ]
    old_paths = [p for _d, p in manifest]
    kept = set(old_paths) - intake_paths
    new_manifest = [(d, renamed(p, old, new)) for d, p in manifest if p in kept]
    plan.tag_files["manifest-sha256.txt"] = format_manifest(new_manifest).encode("utf-8")

    inventory = json.loads(tag[INVENTORY])
    plan.inventory = rename_inventory(inventory, old, new, intake_paths)
    plan.payload_count = plan.inventory["summary"]["file_count"]
    plan.payload_bytes = plan.inventory["summary"]["total_bytes"]
    if plan.payload_count != len(new_manifest):
        raise RenameError(f"inventory lists {plan.payload_count} payload files, manifest {len(new_manifest)}")
    plan.tag_files[INVENTORY] = (json.dumps(plan.inventory, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    submission = json.loads(tag[SUBMISSION])
    submission["source_folder_name"] = new
    submission["staging_folder_name"] = old
    plan.tag_files[SUBMISSION] = (json.dumps(submission, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    if PREVIEW_MANIFEST in tag:
        previews = rekey_preview_manifest(json.loads(tag[PREVIEW_MANIFEST]), old_paths, old, new, kept)
        plan.tag_files[PREVIEW_MANIFEST] = (json.dumps(previews, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    bag_info = set_payload_oxum(tag["bag-info.txt"].decode("utf-8"), plan.payload_bytes, plan.payload_count)
    plan.tag_files["bag-info.txt"] = bag_info.encode("utf-8")

    # tagmanifest: changed tag files get new digests, the moved intake files are tag files now.
    tagmanifest = parse_manifest(tag["tagmanifest-sha256.txt"].decode("utf-8"))
    entries = {p: d for d, p in tagmanifest}
    for rel, content in plan.tag_files.items():
        entries[rel] = sha256_hex(content)
    for src, dst in plan.intake_moves:
        entries[dst] = digests[src]
    plan.tag_files["tagmanifest-sha256.txt"] = format_manifest(sorted(((d, p) for p, d in entries.items()),
                                                                      key=lambda e: e[1])).encode("utf-8")
    return plan


# --- applying it ---------------------------------------------------------------------------------

TAG_KEYS = ("manifest-sha256.txt", "tagmanifest-sha256.txt", "bag-info.txt", INVENTORY, SUBMISSION, PREVIEW_MANIFEST)


def read_tag_files(s3, bucket: str, root: str) -> dict[str, bytes]:
    out = {}
    for rel in TAG_KEYS:
        try:
            out[rel] = s3.get_object(Bucket=bucket, Key=root + rel)["Body"].read()
        except s3.exceptions.NoSuchKey:
            if rel != PREVIEW_MANIFEST:
                raise
    return out


def find_handoff(s3, bucket: str, root: str, manifest: bytes) -> bytes:
    """The bag's hitsave-submission.yaml, wherever an interrupted run left it."""
    folder = payload_folder(parse_manifest(manifest.decode("utf-8")))
    for rel in (f"data/{folder}/{HANDOFF_FILENAME}", f"metadata/{INTAKE_METADATA_SUBDIR}/{HANDOFF_FILENAME}"):
        try:
            return s3.get_object(Bucket=bucket, Key=root + rel)["Body"].read()
        except s3.exceptions.NoSuchKey:
            continue
    raise RenameError(f"no {HANDOFF_FILENAME} in the bag")


def apply_on_wasabi(s3, bucket: str, root: str, plan: BagPlan, *, upload, list_sizes, move_key, move_prefix) -> None:
    """MOVE intake files, MOVE the payload folder, then upload tag files (tagmanifest last)."""
    sizes = list_sizes(s3, bucket=bucket, prefix=root.rstrip("/"))
    for src, dst in plan.intake_moves:
        if src in sizes and dst not in sizes:
            move_key(s3, bucket=bucket, source_key=root + src, destination_key=root + dst)
        elif dst in sizes and src not in sizes:
            continue  # moved by an earlier, interrupted run
        else:
            raise RenameError(f"can't move {src} -> {dst}: source {'present' if src in sizes else 'missing'}, "
                              f"destination {'present' if dst in sizes else 'missing'}")
    old, new = plan.folder_move
    sizes = list_sizes(s3, bucket=bucket, prefix=root.rstrip("/"))
    at_old = [k for k in sizes if k.startswith(old + "/")]
    at_new = [k for k in sizes if k.startswith(new + "/")]
    if at_old and not at_new:
        move_prefix(s3, bucket=bucket, source_prefix=root + old, destination_prefix=root + new)
    elif at_old and at_new:
        raise RenameError(f"both {old}/ and {new}/ have objects; needs a human")
    elif not at_new:
        raise RenameError(f"no payload under {old}/ or {new}/")
    # every manifest entry must now exist at its new key with the inventory's size
    sizes = list_sizes(s3, bucket=bucket, prefix=root.rstrip("/"))
    for rec in plan.inventory["files"]:
        if sizes.get(rec["path"]) != int(rec.get("size_bytes") or 0):
            raise RenameError(f"after MOVE: {rec['path']} missing or wrong size on Wasabi")
    for rel, content in plan.tag_files.items():
        if rel != "tagmanifest-sha256.txt":
            upload(rel, content)
    upload("tagmanifest-sha256.txt", plan.tag_files["tagmanifest-sha256.txt"])


def verify_tag_files(s3, bucket: str, root: str) -> list[str]:
    """Every tagmanifest entry exists with its digest (tag files are small)."""
    problems = []
    tagmanifest = parse_manifest(s3.get_object(Bucket=bucket, Key=root + "tagmanifest-sha256.txt")["Body"].read()
                                 .decode("utf-8"))
    for digest, rel in tagmanifest:
        body = s3.get_object(Bucket=bucket, Key=root + rel)["Body"].read()
        if sha256_hex(body) != digest:
            problems.append(f"tag file digest mismatch: {rel}")
    return problems


def rename_local_copy(bag_dir: Path, plan: BagPlan) -> bool:
    """Same rename for the local working copy under /output/bag, if there is one in the old layout."""
    old_dir = bag_dir / "data" / plan.old_folder
    if not old_dir.is_dir():
        return False
    intake = bag_dir / "metadata" / INTAKE_METADATA_SUBDIR
    intake.mkdir(parents=True, exist_ok=True)
    for src, dst in plan.intake_moves:
        if (bag_dir / src).is_file():
            shutil.move(str(bag_dir / src), str(bag_dir / dst))
    new_dir = bag_dir / "data" / plan.new_folder
    if new_dir.exists():
        raise RenameError(f"local {new_dir} already exists")
    old_dir.rename(new_dir)
    for rel, content in plan.tag_files.items():
        (bag_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (bag_dir / rel).write_bytes(content)
    return True


def inventory_digests(inventory: dict | None) -> dict[str, str]:
    return {str(f.get("path")): str(f.get("sha256") or "") for f in (inventory or {}).get("files") or []}


def ledger_matches_bag(ledger_inventory: dict | None, bag_inventory: dict) -> bool:
    return inventory_digests(ledger_inventory) == inventory_digests(bag_inventory)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game_keys", nargs="*")
    ap.add_argument("--all", action="store_true", help="every complete remote-intake dump bag")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if bool(args.game_keys) == args.all:
        ap.error("give GAME_KEYs or --all")

    from upload_bag_wasabi import load_wasabi_settings, make_s3_client, pg_connect, resolve_config_path
    from preservation_common import wasabi_bucket_for_storage_prefix
    from refresh_bag_previews_wasabi import bump_previews_version
    from wasabi_rename import list_keys_with_sizes, wasabi_move_key, wasabi_move_prefix_verified
    from yaml_config import load_yaml

    ingest_path = Path("/config/preservation/ingest.yaml")
    ingest = load_yaml(ingest_path)
    wasabi, _ = load_wasabi_settings(ingest_path)
    s3 = make_s3_client(wasabi, load_yaml(resolve_config_path(str(wasabi["credentials_file"]), ingest_path.parent.parent)))
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]

    with pg_connect(db) as conn, conn.cursor() as cur:
        sql = ("SELECT game_key, package_storage_prefix, omeka_media_id, inventory_json, intake_source FROM game_ingest "
               "WHERE status = 'complete' AND ingest_kind = 'dump' AND source_path LIKE '/data/intake/ready/%%'")
        if args.all:
            cur.execute(sql + " ORDER BY created_at")
        else:
            cur.execute(sql + " AND game_key = ANY(%s) ORDER BY created_at", (args.game_keys,))
        rows = cur.fetchall()
    if not args.all and len(rows) != len(set(args.game_keys)):
        missing = set(args.game_keys) - {r[0] for r in rows}
        print(f"not complete remote-intake dump packages: {sorted(missing)}", file=sys.stderr)
        return 1

    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-folder-names"
    done = skipped = failed = 0
    for game_key, prefix, media_id, ledger_inv, intake_source in rows:
        if args.limit and done + failed >= args.limit:
            break
        if isinstance(ledger_inv, str):
            ledger_inv = json.loads(ledger_inv)
        root = str(prefix).strip("/") + "/"
        try:
            bucket = wasabi_bucket_for_storage_prefix(wasabi, prefix, ingest_kind="dump")
            tag = read_tag_files(s3, bucket, root)
            plan = plan_bag(tag, find_handoff(s3, bucket, root, tag["manifest-sha256.txt"]),
                            fallback_name=name_from_intake_source(intake_source))
            bag_inv = plan.inventory if plan else json.loads(tag[INVENTORY])
            if plan is None and ledger_matches_bag(ledger_inv, bag_inv):
                skipped += 1
                print(json.dumps({"game_key": game_key, "status": "already_original_name"}))
                continue
            if plan is not None:
                renamed_ledger = rename_inventory(ledger_inv or {}, plan.old_folder, plan.new_folder,
                                                  {s for s, _d in plan.intake_moves})
                if not ledger_matches_bag(renamed_ledger, plan.inventory):
                    raise RenameError("ledger inventory_json doesn't match the bag's inventory.json; not touching it")
                summary = {"game_key": game_key, "from": plan.old_folder, "to": plan.new_folder,
                           "intake_moves": plan.intake_moves, "payload_files": plan.payload_count}
            else:
                summary = {"game_key": game_key, "ledger": "behind the bag (earlier run stopped)"}
            if not args.execute:
                print(json.dumps({**summary, "status": "plan"}, ensure_ascii=False))
                done += 1
                continue

            if plan is not None:
                # the tag files as they were (payload bytes are only moved, never rewritten)
                backup = BACKUP_ROOT / game_key
                for rel, content in tag.items():
                    (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                    (backup / rel).write_bytes(content)
                (backup / "plan.json").write_text(json.dumps(
                    {"prefix": prefix, "bucket": bucket, "from": plan.old_folder, "to": plan.new_folder,
                     "intake_moves": plan.intake_moves}, ensure_ascii=False, indent=2))

                def upload(rel: str, content: bytes) -> None:
                    s3.put_object(Bucket=bucket, Key=root + rel, Body=content,
                                  ContentType="application/json" if rel.endswith(".json") else "text/plain; charset=utf-8")

                apply_on_wasabi(s3, bucket, root, plan, upload=upload, list_sizes=list_keys_with_sizes,
                                move_key=wasabi_move_key, move_prefix=wasabi_move_prefix_verified)
                problems = verify_tag_files(s3, bucket, root)
                if problems:
                    raise RenameError("; ".join(problems))
            summary_inv = bag_inv["summary"]
            with pg_connect(db) as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE game_ingest SET inventory_json = %s::jsonb, file_count = %s, total_bytes = %s, "
                    "updated_at = NOW() WHERE game_key = %s AND status = 'complete'",
                    (json.dumps(bag_inv), summary_inv["file_count"], summary_inv["total_bytes"], game_key),
                )
                conn.commit()
            omeka = None
            if media_id:
                try:
                    bump_previews_version(int(media_id), version)
                    omeka = int(media_id)
                except Exception as e:  # the bag and ledger are right; the viewer cache expires anyway
                    omeka = f"previews_version bump failed: {str(e)[:200]}"
            local = rename_local_copy(BAG_ROOT / game_key, plan) if plan is not None else False
            done += 1
            print(json.dumps({**summary, "status": "renamed", "local_copy": local, "omeka_media": omeka},
                             ensure_ascii=False), flush=True)
        except Exception as e:  # one bad bag must not stop the rest; it's reported and retried next run
            failed += 1
            print(json.dumps({"game_key": game_key, "status": "FAILED", "error": str(e)[:500]}), flush=True)
    print(json.dumps({"renamed" if args.execute else "planned": done, "already": skipped, "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
