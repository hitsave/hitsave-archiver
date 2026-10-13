from pathlib import Path

from preservation_common import bagit_payload_relpath


def test_payload_relpath_preserves_source_folder_name(tmp_path: Path) -> None:
    sip = tmp_path / "F-14_Tomcat"
    sip.mkdir()
    f = sip / "shot.jpg"
    f.write_bytes(b"x")
    assert (
        bagit_payload_relpath(sip, f, preserve_source_folder=True) == "F-14_Tomcat/shot.jpg"
    )
    assert bagit_payload_relpath(sip, f, preserve_source_folder=False) == "shot.jpg"


def test_payload_relpath_nested_files(tmp_path: Path) -> None:
    sip = tmp_path / "The Invasion of Nintendo"
    (sip / "Screenshots").mkdir(parents=True)
    f = sip / "Screenshots" / "a.png"
    f.write_bytes(b"x")
    assert (
        bagit_payload_relpath(sip, f, preserve_source_folder=True)
        == "The Invasion of Nintendo/Screenshots/a.png"
    )
