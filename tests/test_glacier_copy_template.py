"""The CloudFormation template for the Glacier Deep Archive copy (deploy/aws/glacier-copy.yaml)."""
from __future__ import annotations

from pathlib import Path

import yaml

TEMPLATE = Path(__file__).resolve().parents[1] / "deploy" / "aws" / "glacier-copy.yaml"


class _CfnLoader(yaml.SafeLoader):
    pass


def _any_tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {f"Fn::{suffix}" if suffix != "Ref" else "Ref": value}


_CfnLoader.add_multi_constructor("!", _any_tag)


def template() -> dict:
    return yaml.load(TEMPLATE.read_text(), Loader=_CfnLoader)


def resources_of_type(kind: str) -> list[dict]:
    return [r for r in template()["Resources"].values() if r["Type"] == kind]


def bucket_props() -> dict:
    return resources_of_type("AWS::S3::Bucket")[0]["Properties"]


def writer_policy_actions() -> set[str]:
    actions: set[str] = set()
    for user in resources_of_type("AWS::IAM::User"):
        for policy in user["Properties"].get("Policies") or []:
            for stmt in policy["PolicyDocument"]["Statement"]:
                acts = stmt["Action"]
                actions.update([acts] if isinstance(acts, str) else acts)
    return actions


def test_bucket_locks_versions_and_blocks_public_access():
    b = bucket_props()
    assert b["ObjectLockEnabled"] is True
    assert b["ObjectLockConfiguration"]["Rule"]["DefaultRetention"]["Mode"] == "GOVERNANCE"
    assert b["VersioningConfiguration"]["Status"] == "Enabled"
    assert all(b["PublicAccessBlockConfiguration"][k] for k in
               ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"))


def test_lifecycle_aborts_uploads_and_expires_old_versions():
    rules = {r["Id"]: r for r in bucket_props()["LifecycleConfiguration"]["Rules"]}
    assert rules["abort-incomplete-uploads"]["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] == 7
    assert "NoncurrentVersionExpiration" in rules["expire-old-versions"]


def test_writer_cannot_destroy_versions_or_change_the_bucket():
    actions = writer_policy_actions()
    assert actions, "writer user has no policy"
    assert "s3:DeleteObjectVersion" not in actions
    assert "s3:BypassGovernanceRetention" not in actions
    assert not any(a.startswith("s3:PutBucket") or a == "s3:*" for a in actions)


def test_defaults_match_the_decisions():
    params = template()["Parameters"]
    assert params["BucketName"]["Default"] == "hitsave-backup-glacier"
    assert params["RetentionDays"]["Default"] == 365
    assert not resources_of_type("AWS::IAM::AccessKey")
