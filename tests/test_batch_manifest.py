"""batch_key derivation and folder slugs for operator batch files."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from batch_manifest import derive_batch_key, folder_config_slug  # noqa: E402


def test_derive_batch_key_from_prefixed_filename(tmp_path: Path) -> None:
    batch_file = tmp_path / "batch-repcopies-f-5.yml"
    batch_file.write_text("ingest_kind: press_material\nsource_subpath: F\n", encoding="utf-8")
    assert derive_batch_key(batch_file) == "repcopies-f-5"


def test_derive_batch_key_plain_filename(tmp_path: Path) -> None:
    batch_file = tmp_path / "silesia-newimages.yaml"
    batch_file.write_text("ingest_kind: press_material\n", encoding="utf-8")
    assert derive_batch_key(batch_file) == "silesia-newimages"


def test_derive_batch_key_yaml_override_deprecated(tmp_path: Path) -> None:
    batch_file = tmp_path / "batch-custom.yml"
    batch_file.write_text("batch_key: explicit-key\ningest_kind: dump\n", encoding="utf-8")
    assert derive_batch_key(batch_file) == "explicit-key"


def test_folder_config_slug() -> None:
    assert folder_config_slug("F-Zero!!!") == "f-zero"
    assert folder_config_slug("  ") == "game"
