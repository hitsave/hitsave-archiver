from pathlib import Path

from preservation_common import material_root_for_kind, wasabi_package_storage_prefix


def test_analog_wasabi_prefix_follows_folder_tree() -> None:
    rel = "VHS/USA/promotional/Nintendo/The Invasion of Nintendo"
    prefix = wasabi_package_storage_prefix(
        "analog",
        package_id="the-invasion-of-nintendo-01a13000-0000-7000-8000-000000000001",
        sort_letter="T",
        relative_sip_path=rel,
    )
    assert prefix == f"analog/{rel}"


def test_material_root_for_analog() -> None:
    paths = {
        "press_material_root": "/data/press-material",
        "analog_material_root": "/data/analog",
    }
    assert material_root_for_kind("analog", paths) == Path("/data/analog")
    assert material_root_for_kind("press_material", paths) == Path("/data/press-material")
