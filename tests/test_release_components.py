import copy
import json
from unittest.mock import Mock

import pytest
from scripts import release_components as release


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key, value in {'DEPLOY_ENV': 'dev', 'GITHUB_SHA': 'a' * 40, 'AWS_REGION': 'us-east-1', 'AWS_ACCOUNT_ID': '123456789012'}.items():
        monkeypatch.setenv(key, value)
    api_image = '123456789012.dkr.ecr.us-east-1.amazonaws.com/jimmy-dev-api@sha256:' + 'b' * 64
    worker_image = api_image.replace('-api@', '-worker@')
    data = {
        'environment': 'dev', 'api_image': api_image, 'worker_image': worker_image,
        'api_functions': {'projects': 'jimmy-dev-projects'}, 'realtime_functions': {},
        'task_templates': {kind: {'family': f'jimmy-dev-{kind}', 'containerDefinitions': [{'name': 'task', 'image': worker_image}]} for kind in ['worker', 'runner']},
        'workflow_arn': 'arn:aws:states:us-east-1:123456789012:stateMachine:jimmy-dev-research',
        'workflow_definition': {'StartAt': 'plan', 'States': {
            stage: {'Type': 'Task', 'Resource': 'arn:aws:states:::ecs:runTask.sync', 'Parameters': {'TaskDefinition': 'old'}, 'End': True}
            for stage in ['plan', 'run', 'extension_run']
        }},
    }
    monkeypatch.setattr(release, 'contract', lambda: copy.deepcopy(data))
    return data


def test_lambda_release_waits_and_uses_revision_compare_and_swap(deployment, monkeypatch):
    api = Mock()
    api.get_function.side_effect = [
        {'Code': {'ResolvedImageUri': 'old'}, 'Configuration': {'RevisionId': 'revision-before'}},
        {'Code': {'ResolvedImageUri': deployment['api_image']}},
    ]
    monkeypatch.setattr(release, 'client', lambda service: api)
    release.release_lambda('api', 'projects')
    api.update_function_code.assert_called_once_with(FunctionName='jimmy-dev-projects', ImageUri=deployment['api_image'], RevisionId='revision-before')
    assert release.read_json('.deployment/projects-receipt.json')['previous_image'] == 'old'


def test_lambda_release_is_repeatable(deployment, monkeypatch):
    api = Mock()
    api.get_function.return_value = {'Code': {'ResolvedImageUri': deployment['api_image']}}
    monkeypatch.setattr(release, 'client', lambda service: api)
    release.release_lambda('api', 'projects')
    api.update_function_code.assert_not_called()


def test_task_registration_and_workflow_commit_all_pinned_revisions(deployment, monkeypatch):
    ecs = Mock()
    ecs.register_task_definition.side_effect = [
        {'taskDefinition': {'taskDefinitionArn': f'arn:aws:ecs:us-east-1:123456789012:task-definition/jimmy-dev-{kind}:7'}}
        for kind in ['worker', 'runner']
    ]
    sf = Mock()
    sf.describe_state_machine.side_effect = lambda **kwargs: {'definition': sf.update_state_machine.call_args.kwargs['definition']}
    monkeypatch.setattr(release, 'client', lambda service: ecs if service == 'ecs' else sf)
    release.release_task('worker')
    release.release_task('runner')
    release.release_workflow()
    definition = json.loads(sf.update_state_machine.call_args.kwargs['definition'])
    assert definition['States']['plan']['Parameters']['TaskDefinition'].endswith('worker:7')
    assert definition['States']['run']['Parameters']['TaskDefinition'].endswith('runner:7')
    assert definition['States']['extension_run']['Parameters']['TaskDefinition'].endswith('runner:7')
    assert deployment['workflow_definition']['States']['plan']['Parameters']['TaskDefinition'] == 'old'


def test_mismatched_receipt_cannot_publish_workflow(deployment, monkeypatch):
    release.receipt('worker', {'image': 'wrong-digest', 'task_arn': 'old'})
    api = Mock()
    monkeypatch.setattr(release, 'client', lambda service: api)
    with pytest.raises(release.ReleaseError, match='receipts must match'):
        release.release_workflow()
    api.update_state_machine.assert_not_called()


def test_image_artifact_rejects_wrong_environment_and_mutable_tags(deployment):
    release.receipt('unused', {})
    from pathlib import Path
    path = Path('.deployment/api-image.json')
    path.write_text(json.dumps({**release.context(), 'environment': 'prod', 'image': deployment['api_image']}))
    with pytest.raises(release.ReleaseError, match='different commit or environment'):
        release.image_manifest('api')
    with pytest.raises(release.ReleaseError, match='pinned to a digest'):
        release.validate_image(deployment['api_image'].split('@')[0] + ':latest', 'api')
