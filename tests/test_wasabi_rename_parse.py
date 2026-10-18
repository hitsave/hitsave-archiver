from wasabi_rename import _parse_move_result

SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<MoveObjectResult>
  <SourcePrefix>FamitsuWaveDVD/FWD0307/</SourcePrefix>
  <DestinationPrefix>incoming-redump/FWD0307/</DestinationPrefix>
  <MoveObject>
    <SourceKey>FamitsuWaveDVD/FWD0307/FWD0307.iso</SourceKey>
    <DestinationKey>incoming-redump/FWD0307/FWD0307.iso</DestinationKey>
  </MoveObject>
  <MoveCount>1</MoveCount>
</MoveObjectResult>
"""


def test_parse_move_result() -> None:
    parsed = _parse_move_result(SAMPLE)
    assert parsed["move_count"] == 1
    assert parsed["source_prefix"] == "FamitsuWaveDVD/FWD0307/"
    assert parsed["objects"][0]["destination_key"].endswith("FWD0307.iso")
