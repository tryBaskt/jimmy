"""Bucket refresh permissions required by Terraform's AWS provider."""
import fnmatch
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# AWS API names can differ from IAM action names; GetBucket* does not cover
# lifecycle, replication, encryption, or acceleration configuration reads.
BUCKET_READ_ACTIONS = [
    's3:ListBucket', 's3:GetBucketLocation', 's3:GetBucketPolicy', 's3:GetBucketAcl',
    's3:GetBucketCORS', 's3:GetBucketWebsite', 's3:GetBucketVersioning',
    's3:GetAccelerateConfiguration', 's3:GetBucketRequestPayment', 's3:GetBucketLogging',
    's3:GetLifecycleConfiguration', 's3:GetReplicationConfiguration',
    's3:GetEncryptionConfiguration', 's3:GetBucketObjectLockConfiguration',
    's3:GetBucketTagging', 's3:GetBucketPublicAccessBlock', 's3:GetBucketOwnershipControls',
]


def bucket_permissions(environment):
    template = (ROOT / 'infra/github/permissions.json.tftpl').read_text()
    for name, value in {'environment': environment, 'account_id': '123456789012', 'region': 'us-east-1'}.items():
        template = template.replace('${' + name + '}', value)
    return next(s for s in json.loads(template)['Statement'] if s['Sid'] == 'Buckets')


@pytest.mark.parametrize('environment', ['dev', 'prod'])
def test_bucket_policy_covers_terraform_refresh_without_expanding_resource_scope(environment):
    permissions = bucket_permissions(environment)
    assert permissions['Effect'] == 'Allow'
    assert set(permissions['Resource']) == {
        f'arn:aws:s3:::jimmy-{environment}-state-123456789012',
        f'arn:aws:s3:::jimmy-{environment}-web-123456789012',
    }
    for action in BUCKET_READ_ACTIONS:
        assert any(fnmatch.fnmatchcase(action, pattern) for pattern in permissions['Action']), action
