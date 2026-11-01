"""Resource refresh permissions required by Terraform's AWS provider."""
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


def deployment_permissions(environment):
    template = (ROOT / 'infra/github/permissions.json.tftpl').read_text()
    for name, value in {'environment': environment, 'account_id': '123456789012', 'region': 'us-east-1'}.items():
        template = template.replace('${' + name + '}', value)
    return json.loads(template)['Statement']


@pytest.mark.parametrize('environment', ['dev', 'prod'])
def test_bucket_policy_covers_terraform_refresh_without_expanding_resource_scope(environment):
    permissions = next(s for s in deployment_permissions(environment) if s['Sid'] == 'Buckets')
    assert permissions['Effect'] == 'Allow'
    assert set(permissions['Resource']) == {
        f'arn:aws:s3:::jimmy-{environment}-state-123456789012',
        f'arn:aws:s3:::jimmy-{environment}-web-123456789012',
    }
    for action in BUCKET_READ_ACTIONS:
        assert any(fnmatch.fnmatchcase(action, pattern) for pattern in permissions['Action']), action


@pytest.mark.parametrize('environment', ['dev', 'prod'])
def test_cognito_refresh_can_read_mfa_configuration_only_for_tagged_environment_pools(environment):
    permissions = next(s for s in deployment_permissions(environment) if s['Sid'] == 'AuthResources')
    assert 'cognito-idp:GetUserPoolMfaConfig' in permissions['Action']
    assert permissions['Resource'] == ['arn:aws:cognito-idp:us-east-1:123456789012:userpool/*']
    assert permissions['Condition'] == {'StringEquals': {
        'aws:ResourceTag/Application': 'jimmy', 'aws:ResourceTag/Environment': environment,
    }}


@pytest.mark.parametrize('environment', ['dev', 'prod'])
def test_agentcore_creation_includes_default_endpoint_and_tag_dependencies(environment):
    permissions = deployment_permissions(environment)
    runtime = next(s for s in permissions if s['Sid'] == 'CreateRuntimes')
    endpoint = next(s for s in permissions if s['Sid'] == 'CreateRuntimeEndpointAndTags')
    assert 'bedrock-agentcore:CreateAgentRuntime' in runtime['Action']
    assert set(endpoint['Action']) == {
        'bedrock-agentcore:CreateAgentRuntimeEndpoint', 'bedrock-agentcore:TagResource',
    }
    # The service authorizes auto-creation on runtime/* before the ID exists.
    assert endpoint['Resource'] == ['arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/*']
    assert endpoint['Condition'] == runtime['Condition'] == {'StringEquals': {
        'aws:RequestTag/Application': 'jimmy', 'aws:RequestTag/Environment': environment,
    }}


@pytest.mark.parametrize('environment', ['dev', 'prod'])
def test_runtime_can_tag_generated_identity_only_in_its_environment(environment):
    statements = deployment_permissions(environment)
    action = 'bedrock-agentcore:TagResource'
    prefix = 'arn:aws:bedrock-agentcore:us-east-1:123456789012:workload-identity-directory/default'
    resource = f'{prefix}/workload-identity/jimmy_{environment}_paper_replication-vjLYbs87L1'
    # Internal identity creation must work without request-tag context. Runtime
    # creation retains its own required Application and Environment tags.
    def allowed_without_context(arn):
        return any(s['Effect'] == 'Allow' and not s.get('Condition')
                   and any(fnmatch.fnmatchcase(action, a) for a in s['Action'])
                   and any(fnmatch.fnmatchcase(arn, r) for r in
                           ([s['Resource']] if isinstance(s['Resource'], str) else s['Resource']))
                   for s in statements)

    assert allowed_without_context(resource)
    # AgentCore also authorizes tagging against the shared default directory.
    assert allowed_without_context(prefix)
    assert not allowed_without_context(prefix.replace('/default', '/other'))
    assert not allowed_without_context(prefix.replace('us-east-1', 'us-west-2'))
    assert not allowed_without_context(prefix.replace('123456789012', '999999999999'))
    assert not allowed_without_context(resource.replace(f'jimmy_{environment}_', 'other_'))
    other_environment = 'prod' if environment == 'dev' else 'dev'
    assert not allowed_without_context(resource.replace(f'jimmy_{environment}_', f'jimmy_{other_environment}_'))
    assert not allowed_without_context(resource.replace('us-east-1', 'us-west-2'))
    assert not allowed_without_context(resource.replace('123456789012', '999999999999'))


@pytest.mark.parametrize('environment', ['dev', 'prod'])
def test_repository_policy_updates_are_limited_to_environment_api_repositories(environment):
    statement = next(s for s in deployment_permissions(environment) if s['Sid'] == 'ApiRepositoryPolicies')
    assert statement['Action'] == ['ecr:SetRepositoryPolicy']
    assert statement['Resource'] == [f'arn:aws:ecr:us-east-1:123456789012:repository/jimmy-{environment}-*-api']
