"""rename_bag_payload_folders: old-layout bags (data/<ready slug>/ with intake files in the payload)
become data/<original name>/ with intake files in metadata/intake/, and stay valid BagIt."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import bagit
import pytest
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rename_bag_payload_folders as rb  # noqa: E402

SLUG = "dvd-video-making-of-fahrenheit-the-japan"
ORIGINAL = "Making of Fahrenheit, The (Japan) 100%"
ROOTKEY = "dumps/Redump/DVD-Video/Bonus Discs/M/pkg-1/"


def _jpg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buf, "JPEG")
    return buf.getvalue()


def _old_bag(tmp_path: Path) -> Path:
    """A bag as built before original folder names, with previews keyed f<N> by position."""
    bag = tmp_path / "bag"
    top = bag / SLUG
    (top / "Scans").mkdir(parents=True)
    (top / "!submissionInfo.txt").write_text("info")
    (top / "DISC.iso").write_bytes(b"iso" * 10)
    (top / "game.ingest.yaml").write_text("ingest_kind: dump\n")
    (top / "hitsave-submission.yaml").write_text(
        yaml.safe_dump({"game_key": SLUG, "redump": {"package_folder_name": ORIGINAL}})
    )
    (top / "Scans" / "back.jpg").write_bytes(_jpg())
    (top / "zz-after-yaml.jpg").write_bytes(_jpg())  # sorts after the intake files: its key shifts
    bagit.make_bag(str(bag), bag_info={"Bag-Software": "test"}, checksums=["sha256"])
    paths = sorted(p.relative_to(bag).as_posix() for p in (bag / "data").rglob("*") if p.is_file())
    keys = rb.viewer_keys(paths)
    meta = bag / "metadata"
    (meta / "hitsave" / "thumbnails" / "grid").mkdir(parents=True)
    images = [p for p in paths if p.endswith(".jpg")]
    preview = {
        "manifest_version": 1,
        "grid": {keys[p]: f"metadata/hitsave/thumbnails/grid/{keys[p]}.jpg" for p in images},
        "display": {keys[p]: f"metadata/hitsave/display/{keys[p]}.jpg" for p in images},
        "files": {keys[p]: {"path": p, "size": 1, "scan": "Scans" in p} for p in images},
        "gallery_file_keys": ["menu"] + [keys[p] for p in images],
    }
    preview["grid"]["menu"] = "metadata/hitsave/thumbnails/grid/menu.jpg"
    for k in preview["grid"]:
        (meta / "hitsave" / "thumbnails" / "grid" / f"{k}.jpg").write_bytes(_jpg())
    (meta / "hitsave" / "preview-manifest.json").write_text(json.dumps(preview))
    inv_files = [{"path": p, "size_bytes": (bag / p).stat().st_size, "sha256": rb.sha256_hex((bag / p).read_bytes()),
                  "mime": "x"} for p in paths]
    (meta / "inventory.json").write_text(json.dumps({"files": inv_files, "summary": {"file_count": len(paths)}}))
    (meta / "submission.json").write_text(json.dumps({"source_folder_name": SLUG}))
    bagit.Bag(str(bag)).save(manifests=True)
    bagit.Bag(str(bag)).validate()
    return bag


class FakeS3:
    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise FakeS3.exceptions.NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}


def _upload_bag(bag: Path) -> FakeS3:
    return FakeS3({ROOTKEY + p.relative_to(bag).as_posix(): p.read_bytes() for p in bag.rglob("*") if p.is_file()})


def _ops(s3: FakeS3):
    def list_sizes(_s3, *, bucket, prefix):
        root = prefix.strip("/") + "/"
        return {k[len(root):]: len(v) for k, v in s3.objects.items() if k.startswith(root)}

    def move_key(_s3, *, bucket, source_key, destination_key):
        s3.objects[destination_key] = s3.objects.pop(source_key)

    def move_prefix(_s3, *, bucket, source_prefix, destination_prefix):
        src, dst = source_prefix.rstrip("/") + "/", destination_prefix.rstrip("/") + "/"
        for k in [k for k in s3.objects if k.startswith(src)]:
            s3.objects[dst + k[len(src):]] = s3.objects.pop(k)

    def upload(rel, content):
        s3.objects[ROOTKEY + rel] = content

    return dict(upload=upload, list_sizes=list_sizes, move_key=move_key, move_prefix=move_prefix)


def _download(s3: FakeS3, dest: Path) -> Path:
    for key, body in s3.objects.items():
        p = dest / key[len(ROOTKEY):]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    return dest


def _plan(s3: FakeS3) -> rb.BagPlan | None:
    tag = rb.read_tag_files(s3, "b", ROOTKEY)
    return rb.plan_bag(tag, rb.find_handoff(s3, "b", ROOTKEY, tag["manifest-sha256.txt"]))


def test_old_bag_is_renamed_and_still_valid(tmp_path) -> None:
    s3 = _upload_bag(_old_bag(tmp_path))
    plan = _plan(s3)
    assert (plan.old_folder, plan.new_folder) == (SLUG, ORIGINAL)
    assert plan.intake_moves == [
        (f"data/{SLUG}/game.ingest.yaml", "metadata/intake/game.ingest.yaml"),
        (f"data/{SLUG}/hitsave-submission.yaml", "metadata/intake/hitsave-submission.yaml"),
    ]
    rb.apply_on_wasabi(s3, "b", ROOTKEY, plan, **_ops(s3))
    out = _download(s3, tmp_path / "after")
    bag = bagit.Bag(str(out))
    bag.validate()  # payload manifest, tagmanifest and Payload-Oxum all agree
    assert (out / "data" / ORIGINAL / "DISC.iso").is_file()
    assert not (out / "data" / SLUG).exists()
    assert (out / "metadata" / "intake" / "hitsave-submission.yaml").is_file()
    assert bag.info["Payload-Oxum"].endswith(".4")
    inv = json.loads((out / "metadata" / "inventory.json").read_text())
    assert inv["summary"]["file_count"] == 4 and all(f["path"].startswith(f"data/{ORIGINAL}/") for f in inv["files"])
    assert json.loads((out / "metadata" / "submission.json").read_text())["source_folder_name"] == ORIGINAL
    assert rb.verify_tag_files(s3, "b", ROOTKEY) == []


def test_preview_keys_follow_their_files(tmp_path) -> None:
    old = _old_bag(tmp_path)
    before = json.loads((old / "metadata/hitsave/preview-manifest.json").read_text())
    s3 = _upload_bag(old)
    rb.apply_on_wasabi(s3, "b", ROOTKEY, _plan(s3), **_ops(s3))
    out = _download(s3, tmp_path / "after")
    after = json.loads((out / "metadata/hitsave/preview-manifest.json").read_text())
    inv_paths = [f["path"] for f in json.loads((out / "metadata/inventory.json").read_text())["files"]]
    viewer = rb.viewer_keys(inv_paths)  # what omeka-dip-viewer will call each file now
    for old_key, rec in before["files"].items():
        new_path = rb.renamed(rec["path"], SLUG, ORIGINAL)
        new_key = viewer[new_path]
        assert after["files"][new_key]["path"] == new_path
        assert after["grid"][new_key] == before["grid"][old_key]  # same thumbnail file
        assert after["display"][new_key] == before["display"][old_key]
    assert after["grid"]["menu"] == before["grid"]["menu"] and after["gallery_file_keys"][0] == "menu"
    # the file sorted after the intake files really got a new key
    old_zz = next(k for k, r in before["files"].items() if r["path"].endswith("zz-after-yaml.jpg"))
    new_zz = viewer[f"data/{ORIGINAL}/zz-after-yaml.jpg"]
    assert old_zz != new_zz


def test_rerun_is_a_no_op_and_interrupted_runs_resume(tmp_path) -> None:
    s3 = _upload_bag(_old_bag(tmp_path))
    plan = _plan(s3)
    ops = _ops(s3)
    # interrupted after the intake MOVEs: the rerun plans the same rename and finishes it
    for src, dst in plan.intake_moves:
        ops["move_key"](s3, bucket="b", source_key=ROOTKEY + src, destination_key=ROOTKEY + dst)
    again = _plan(s3)
    assert again.tag_files == plan.tag_files
    rb.apply_on_wasabi(s3, "b", ROOTKEY, again, **ops)
    bagit.Bag(str(_download(s3, tmp_path / "after"))).validate()
    assert _plan(s3) is None  # already under the original name


def test_refuses_without_a_recorded_name(tmp_path) -> None:
    bag = _old_bag(tmp_path)
    s3 = _upload_bag(bag)
    s3.objects[ROOTKEY + f"data/{SLUG}/hitsave-submission.yaml"] = yaml.safe_dump({"redump": {}}).encode()
    with pytest.raises(rb.RenameError, match="no original folder name"):
        _plan(s3)


def test_manifest_path_encoding_round_trips() -> None:
    p = "data/100% Pure (Japan)/a\nb.txt"
    assert rb.decode_manifest_path(rb.encode_manifest_path(p)) == p
    assert rb.encode_manifest_path("data/100% x") == "data/100% x"  # as bagit-python writes it


def test_ledger_consistency_check() -> None:
    a = {"files": [{"path": "data/x/a", "sha256": "1"}]}
    assert rb.ledger_matches_bag(a, a)
    assert not rb.ledger_matches_bag({"files": []}, a)


def test_local_copy_renamed(tmp_path) -> None:
    old = _old_bag(tmp_path)
    s3 = _upload_bag(old)
    plan = _plan(s3)
    assert rb.rename_local_copy(old, plan) is True
    bagit.Bag(str(old)).validate()
    assert (old / "data" / ORIGINAL / "DISC.iso").is_file()
    assert rb.rename_local_copy(old, plan) is False  # nothing left in the old layout


def test_local_import_falls_back_to_intake_source_name(tmp_path) -> None:
    s3 = _upload_bag(_old_bag(tmp_path))
    s3.objects[ROOTKEY + f"data/{SLUG}/hitsave-submission.yaml"] = yaml.safe_dump({"switch": {"game_title": "Aka"}}).encode()
    tag = rb.read_tag_files(s3, "b", ROOTKEY)
    handoff = rb.find_handoff(s3, "b", ROOTKEY, tag["manifest-sha256.txt"])
    name = rb.name_from_intake_source("local:/tank/video/dumps/Switch/Hit Save/Submitted/Aka")
    assert name == "Aka" and rb.name_from_intake_source("wasabi:redump/X") is None
    assert rb.plan_bag(tag, handoff, fallback_name=name).new_folder == "Aka"
