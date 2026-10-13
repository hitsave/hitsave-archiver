from preservation_common import (
    resolve_wasabi_bucket,
    wasabi_bucket_config_key,
    wasabi_bucket_for_storage_prefix,
)


def test_wasabi_bucket_config_key_dump_uses_dumps():
    assert wasabi_bucket_config_key("dump") == "dumps"
    assert wasabi_bucket_config_key("press_material") == "press_material"
    assert wasabi_bucket_config_key("other") == "other"
    assert wasabi_bucket_config_key("analog") == "analog"


def test_resolve_wasabi_bucket_kind_map():
    wasabi = {
        "buckets": {
            "press_material": "press-bkt",
            "dumps": "dump-bkt",
            "other": "other-bkt",
            "analog": "analog-bkt",
        },
    }
    assert resolve_wasabi_bucket(wasabi, "press_material") == "press-bkt"
    assert resolve_wasabi_bucket(wasabi, "dump") == "dump-bkt"
    assert resolve_wasabi_bucket(wasabi, "other") == "other-bkt"
    assert resolve_wasabi_bucket(wasabi, "analog") == "analog-bkt"


def test_resolve_wasabi_bucket_no_fallback():
    wasabi = {"bucket": "legacy", "buckets": {"press_material": "press-bkt"}}
    try:
        resolve_wasabi_bucket(wasabi, "dump")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "dumps" in str(e)
    try:
        resolve_wasabi_bucket({}, "dump")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "wasabi.buckets" in str(e)


def test_wasabi_bucket_for_storage_prefix_infers_from_path():
    wasabi = {
        "buckets": {
            "press_material": "press-bkt",
            "dumps": "dump-bkt",
            "other": "other-bkt",
            "analog": "analog-bkt",
        },
    }
    assert (
        wasabi_bucket_for_storage_prefix(wasabi, "press-material/F/foo-uuid")
        == "press-bkt"
    )
    assert wasabi_bucket_for_storage_prefix(wasabi, "dumps/PS2/Games/foo-uuid") == "dump-bkt"
    assert (
        wasabi_bucket_for_storage_prefix(wasabi, "other/X/foo-uuid") == "other-bkt"
    )
    assert (
        wasabi_bucket_for_storage_prefix(wasabi, "analog/X/foo-uuid") == "analog-bkt"
    )


def test_wasabi_bucket_for_storage_prefix_unknown_prefix_fails():
    wasabi = {"buckets": {"press_material": "p", "dumps": "d"}}
    try:
        wasabi_bucket_for_storage_prefix(wasabi, "experiments/foo")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "Cannot infer" in str(e)
