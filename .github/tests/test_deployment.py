"""Exercise the actual workflow shell with local stand-ins for cloud commands."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow():
    return yaml.load((ROOT / ".github/workflows/deploy.yml").read_text(), Loader=yaml.BaseLoader)


def step(job, name):
    return next(s for s in workflow()["jobs"][job]["steps"] if s.get("name") == name)["run"]


def action(name):
    definition = yaml.load((ROOT / f".github/actions/{name}/action.yml").read_text(), Loader=yaml.BaseLoader)
    return definition["runs"]["steps"][0]["run"]


STUB = r'''
import json, os, sys
from pathlib import Path
command = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps([command, *args]) + "\n")
if command == "aws":
    if args[:2] == ["sts", "get-caller-identity"]:
        print("123456789012")
    elif args[:2] == ["s3api", "head-object"] and os.getenv("STATE_HEAD_RESULT", "exists") != "exists":
        print("(403) Forbidden" if os.getenv("STATE_HEAD_RESULT") == "denied" else "(404) Not Found", file=sys.stderr)
        sys.exit(254)
    elif args[:2] == ["s3api", "get-bucket-tagging"]:
        tags = {"Application": "jimmy", "Environment": "dev", "ManagedBy": "terraform"}
        if os.getenv("INVALID_BUCKET_TAGS"):
            tags["Environment"] = "prod"
        print(json.dumps({"TagSet": [{"Key": k, "Value": v} for k, v in tags.items()]}))
    elif args[:2] == ["s3api", "head-bucket"] and os.getenv("HEAD_RESULT") != "exists":
        print("(403) Forbidden" if os.getenv("HEAD_RESULT") == "denied" else "(404) Not Found", file=sys.stderr)
        sys.exit(254)
    elif args[:2] == ["ecr", "describe-images"] and os.getenv("IMAGE_RESULT") != "exists":
        print("AccessDeniedException" if os.getenv("IMAGE_RESULT") == "denied" else "ImageNotFoundException", file=sys.stderr)
        sys.exit(254)
elif command == "terraform":
    if args[1] == os.getenv("FAIL_TERRAFORM"):
        sys.exit(1)
    if args[1:3] == ["state", "pull"]:
        print(os.environ["FOUNDATION_STATE"])
    if args[1] == "output":
        print(json.dumps({"environment": "dev", "runtime_arn": "arn:runtime"}))
elif command == "curl":
    if args[-1].endswith("/health"):
        print('{"status":"ok"}')
    elif args[-1].endswith("/me"):
        print(os.getenv("ANONYMOUS_STATUS", "401"), end="")
    else:
        print(json.dumps({"clientId": os.getenv("PUBLISHED_CLIENT", "dev-client")}))
'''


@pytest.fixture
def runner(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    for name in ["aws", "docker", "terraform", "curl"]:
        path = binary / name
        path.write_text(f"#!{sys.executable}\n" + STUB)
        path.chmod(0o755)
    foundation = {"environment": "dev", "api_url": "https://api.example", "web_origin": "https://web.example",
                  "frontend_bucket": "dev-web", "distribution_id": "distribution", "repositories": {
        "paper_replication": {"api": "registry.example/jimmy-dev-paper-replication-api", "runtime": "registry.example/jimmy-dev-paper-replication-runtime"},
        "second_flow": {"api": "registry.example/jimmy-dev-second-flow-api", "runtime": "registry.example/jimmy-dev-second-flow-runtime"},
    }}
    foundation["workflows"] = [
        {"id": "paper_replication", "name": "Paper replication", "api_prefix": "/api/paper-replication", "requires_openai": True},
        {"id": "second_flow", "name": "Second flow", "api_prefix": "/api/second-flow", "requires_openai": False},
    ]
    (tmp_path / "foundation.json").write_text(json.dumps(foundation))
    for name in ["outputs", "environment", "commands"]:
        (tmp_path / name).touch()
    env = os.environ | {
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "GITHUB_REF": "refs/heads/dev", "GITHUB_SHA": "commit123", "JIMMY_ENV": "dev",
        "GITHUB_OUTPUT": str(tmp_path / "outputs"), "GITHUB_ENV": str(tmp_path / "environment"),
        "COMMAND_LOG": str(tmp_path / "commands"), "AWS_REGION": "us-east-1", "AWS_ACCOUNT_ID": "123456789012", "DEPLOY_ENV": "dev",
        "STATE_BUCKET": "state-bucket", "INCLUDE_AUTH": "false", "WORKFLOW_ID": "",
    }

    def execute(body, **overrides):
        return subprocess.run(["bash", "-euo", "pipefail", "-c", body], cwd=tmp_path,
                              env=env | overrides, capture_output=True, text=True)

    def calls():
        return [json.loads(line) for line in (tmp_path / "commands").read_text().splitlines()]
    return tmp_path, execute, calls


@pytest.mark.parametrize("region", ["us-east-1", "us-west-2"])
def test_state_bootstrap_preserves_region_and_security(runner, region):
    folder, execute, calls = runner
    result = execute(step("provision-infrastructure", "Ensure environment state bucket"), AWS_REGION=region)
    assert result.returncode == 0, result.stderr
    create = next(c for c in calls() if c[1:3] == ["s3api", "create-bucket"])
    assert ("--create-bucket-configuration" in create) == (region != "us-east-1")
    head = next(c for c in calls() if c[1:3] == ["s3api", "head-bucket"])
    assert head[head.index("--expected-bucket-owner") + 1] == "123456789012"
    assert {c[2] for c in calls() if c[1] == "s3api"} >= {
        "put-bucket-versioning", "put-public-access-block", "put-bucket-encryption",
    }
    assert (folder / "outputs").read_text() == "bucket=jimmy-dev-state-123456789012\n"


def test_state_bootstrap_does_not_treat_access_denied_as_missing(runner):
    _, execute, calls = runner
    result = execute(step("provision-infrastructure", "Ensure environment state bucket"), HEAD_RESULT="denied")
    assert result.returncode != 0
    assert all(c[2] not in {"create-bucket", "put-bucket-versioning"} for c in calls())


@pytest.mark.parametrize("body", [action("deployment-config"), action("build-image"),
                                  step("provision-infrastructure", "Ensure environment state bucket")])
def test_feature_branch_cannot_provision_or_publish(runner, body):
    _, execute, calls = runner
    assert execute(body, GITHUB_REF="refs/heads/feature", WORKFLOW_ID="paper_replication", COMPONENT="api").returncode != 0
    assert calls() == []


def test_api_configuration_rejects_outputs_from_another_environment(runner):
    folder, execute, calls = runner
    (folder / "auth.json").write_text('{"environment":"prod"}')
    result = execute(action("deployment-config"), INCLUDE_AUTH="true", WORKFLOW_ID="paper_replication")
    assert result.returncode != 0
    assert calls() == []
    assert "TF_VAR_auth=" not in (folder / "environment").read_text()


def test_api_configuration_loads_both_component_outputs(runner):
    folder, execute, _ = runner
    (folder / "auth.json").write_text('{"environment":"dev","cognito_client_id":"dev-client"}')
    result = execute(action("deployment-config"), INCLUDE_AUTH="true", WORKFLOW_ID="paper_replication")
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in (folder / "environment").read_text().splitlines())
    assert json.loads(values["TF_VAR_auth"])["cognito_client_id"] == "dev-client"
    assert json.loads(values["TF_VAR_workflow"])["id"] == "paper_replication"
    assert values["DEPLOY_ENV"] == "dev"


@pytest.mark.parametrize("workflow_id", ["not_registered", "../outside"])
def test_unregistered_workflow_cannot_publish_or_provision(runner, workflow_id):
    _, execute, calls = runner
    assert execute(action("deployment-config"), WORKFLOW_ID=workflow_id).returncode != 0
    assert execute(action("build-image"), WORKFLOW_ID=workflow_id, COMPONENT="api").returncode != 0
    assert calls() == []


@pytest.mark.parametrize("workflow_id,component", [("paper_replication", "api"), ("paper_replication", "runtime"), ("second_flow", "api"), ("second_flow", "runtime")])
def test_builds_only_the_selected_workflow_component(runner, workflow_id, component):
    folder, execute, calls = runner
    result = execute(action("build-image"), WORKFLOW_ID=workflow_id, COMPONENT=component)
    assert result.returncode == 0, result.stderr
    builds = [c for c in calls() if c[0] == "docker"]
    assert len(builds) == 1
    dockerfile = "Dockerfile.api" if component == "api" else "Dockerfile"
    assert f"services/{workflow_id}/{dockerfile}" in builds[0]
    assert "linux/arm64" in builds[0]
    repository = workflow_id.replace("_", "-")
    assert (folder / "outputs").read_text() == f"image_uri=registry.example/jimmy-dev-{repository}-{component}:commit123\n"


def test_existing_immutable_image_is_reused(runner):
    _, execute, calls = runner
    assert execute(action("build-image"), WORKFLOW_ID="paper_replication", COMPONENT="api", IMAGE_RESULT="exists").returncode == 0
    assert not any(c[0] == "docker" for c in calls())


def test_image_access_denied_blocks_build_and_publication(runner):
    folder, execute, calls = runner
    assert execute(action("build-image"), WORKFLOW_ID="paper_replication", COMPONENT="api", IMAGE_RESULT="denied").returncode != 0
    assert not any(c[0] == "docker" for c in calls())
    assert (folder / "outputs").read_text() == ""


def test_failed_auth_apply_does_not_publish_outputs(runner):
    folder, execute, calls = runner
    result = execute(step("provision-auth", "Provision Cognito and JWT authorizer"), FAIL_TERRAFORM="apply")
    assert result.returncode != 0
    assert not (folder / "auth.json").exists()
    assert not any("output" in c for c in calls())


def test_frontend_configuration_uses_environment_auth_outputs(runner):
    folder, execute, _ = runner
    (folder / "apps/web/dist").mkdir(parents=True)
    (folder / "auth.json").write_text('{"environment":"dev","cognito_client_id":"dev-client","cognito_domain":"https://auth.example"}')
    result = execute(step("provision-frontend", "Configure frontend"))
    assert result.returncode == 0, result.stderr
    assert json.loads((folder / "apps/web/dist/config.json").read_text()) == {
        "apiUrl": "https://api.example", "clientId": "dev-client", "cognitoDomain": "https://auth.example",
        "workflows": json.loads((folder / "foundation.json").read_text())["workflows"],
    }


@pytest.mark.parametrize("status,client,success", [("401", "dev-client", True), ("200", "dev-client", False),
                                                 ("401", "other-client", False)])
def test_frontend_smoke_checks_authentication_and_configuration(runner, status, client, success):
    folder, execute, _ = runner
    (folder / "auth.json").write_text('{"cognito_client_id":"dev-client"}')
    result = execute(step("provision-frontend", "Verify endpoints"), ANONYMOUS_STATUS=status, PUBLISHED_CLIENT=client)
    assert (result.returncode == 0) == success, result.stderr


def test_workflow_dependencies_state_and_guards_remain_isolated():
    definition = workflow()
    assert definition["on"] == {"push": {"branches": ["dev", "prod"]}}
    jobs = definition["jobs"]
    assert jobs["provision-auth"]["needs"] == "provision-infrastructure"
    assert set(jobs["provision-workflows"]["needs"]) == {"provision-infrastructure", "provision-auth"}
    assert "provision-workflows" in jobs["provision-frontend"]["needs"]
    assert "fromJSON" in jobs["provision-workflows"]["strategy"]["matrix"]["workflow"]
    for job_name, component in [("provision-auth", "auth"), ("provision-workflows", "workflows")]:
        job = jobs[job_name]
        assert job["environment"] == "${{ github.ref_name }}"
        steps = job["steps"]
        prepare = next(i for i, s in enumerate(steps) if s.get("uses") == "./.github/actions/deployment-config")
        apply = next(i for i, s in enumerate(steps) if " apply " in s.get("run", ""))
        assert prepare < apply
        key = "key=workflows/$WORKFLOW_ID/terraform.tfstate" if component == "workflows" else "key=auth/terraform.tfstate"
        assert key in steps[apply]["run"]
        assert "-target" not in steps[apply]["run"]
    assert not any("build-image" in s.get("uses", "") for s in jobs["provision-auth"]["steps"])


def test_embedded_shell_has_valid_syntax():
    sources = [s["run"] for job in workflow()["jobs"].values() for s in job["steps"] if "run" in s]
    sources += [action("deployment-config"), action("build-image"), action("recover-foundation")]
    for source in sources:
        result = subprocess.run(["bash", "-n"], input=source, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_foundation_receives_github_key_only_as_ephemeral_terraform_input():
    definition = workflow()
    jobs = definition['jobs']
    provision = next(s for s in jobs['provision-infrastructure']['steps'] if s.get('name') == 'Provision foundation')
    assert provision['env']['TF_VAR_openai_api_key'] == '${{ secrets.OPENAI_API_KEY }}'
    assert provision['env']['TF_VAR_openai_api_key_version'] == '${{ github.run_number }}'
    assert 'openai_api_key' not in provision['run']
    for job in jobs.values():
        assume = next(s for s in job['steps'] if s.get('uses') == 'aws-actions/configure-aws-credentials@v4')
        assert 'secrets.AWS_ACCOUNT_ID' in assume['with']['role-to-assume']
        assert 'jimmy-{1}-github-deploy' in assume['with']['role-to-assume']
        assert 'secrets.AWS_REGION' in job['env']['AWS_REGION']
    for name in ['provision-auth', 'provision-workflows', 'provision-frontend']:
        assert 'secrets.OPENAI_API_KEY' not in json.dumps(jobs[name])
    assert 'OPENAI_API_KEY_SECRET_ARN' not in json.dumps(definition)


def test_failed_foundation_plan_cannot_apply_or_publish_outputs(runner):
    folder, execute, calls = runner
    (folder / 'foundation.json').unlink()
    result = execute(step('provision-infrastructure', 'Provision foundation'), FAIL_TERRAFORM='plan')
    assert result.returncode != 0
    assert not (folder / 'foundation.json').exists()
    assert not any('apply' in c or 'output' in c for c in calls())


def test_state_bucket_rejects_a_different_aws_account(runner):
    _, execute, calls = runner
    result = execute(step('provision-infrastructure', 'Ensure environment state bucket'), AWS_ACCOUNT_ID='999999999999')
    assert result.returncode != 0
    assert not any(c[1] == 's3api' for c in calls())


def recover_bucket(runner, state=None, **overrides):
    _, execute, calls = runner
    state = state if state is not None else {
        'lineage': '4717025d-f1e8-392e-2999-cd183598176a', 'serial': 2,
        'resources': [{'mode': 'managed', 'type': 'aws_s3_bucket', 'name': 'web',
                       'instances': [{'status': 'tainted', 'attributes': {
                           'id': 'jimmy-dev-web-123456789012', 'bucket': 'jimmy-dev-web-123456789012'}}]}],
    }
    environment = {'FOUNDATION_STATE': json.dumps(state),
                   'STATE_BUCKET': 'jimmy-dev-state-123456789012', 'HEAD_RESULT': 'exists'} | overrides
    return execute(action('recover-foundation'), **environment), calls


def test_known_failed_bucket_is_untainted_with_locking_and_ownership_checks(runner):
    result, calls = recover_bucket(runner)
    assert result.returncode == 0, result.stderr
    untaint = next(c for c in calls() if 'untaint' in c)
    assert untaint == ['terraform', '-chdir=infra/foundation', 'untaint', '-lock-timeout=60s', 'aws_s3_bucket.web']
    head = next(c for c in calls() if c[1:3] == ['s3api', 'head-bucket'])
    assert head[head.index('--bucket') + 1] == 'jimmy-dev-web-123456789012'
    assert head[head.index('--expected-bucket-owner') + 1] == '123456789012'
    assert not any('delete-bucket' in c or 'apply' in c for c in calls())


@pytest.mark.parametrize('change', [
    {'serial': 3}, {'lineage': 'another-state'}, {'resources': []},
    {'resources': [{'mode': 'managed', 'type': 'aws_s3_bucket', 'name': 'web',
                    'instances': [{'attributes': {'id': 'jimmy-dev-web-123456789012',
                                                  'bucket': 'jimmy-dev-web-123456789012'}}]}]},
    {'resources': [{'mode': 'managed', 'type': 'aws_s3_bucket', 'name': 'web',
                    'instances': [{'status': 'tainted', 'attributes': {'id': 'other', 'bucket': 'other'}}]}]},
])
def test_recovery_never_clears_unknown_or_healthy_state(runner, change):
    state = {'lineage': '4717025d-f1e8-392e-2999-cd183598176a', 'serial': 2} | change
    result, calls = recover_bucket(runner, state)
    assert result.returncode == 0, result.stderr
    assert not any('untaint' in c for c in calls())


@pytest.mark.parametrize('reference', ['refs/heads/feature', 'refs/heads/prod'])
def test_dev_recovery_does_not_touch_other_branches(runner, reference):
    result, calls = recover_bucket(runner, GITHUB_REF=reference)
    assert result.returncode == 0
    assert calls() == []


@pytest.mark.parametrize('overrides', [{'INVALID_BUCKET_TAGS': '1'}, {'STATE_HEAD_RESULT': 'denied'},
                                      {'FAIL_TERRAFORM': 'state'}, {'HEAD_RESULT': 'denied'},
                                      {'STATE_BUCKET': 'another-state-bucket'}])
def test_recovery_aborts_when_state_or_bucket_cannot_be_verified(runner, overrides):
    result, calls = recover_bucket(runner, **overrides)
    assert result.returncode != 0
    assert not any('untaint' in c for c in calls())


def test_recovery_skips_fresh_environment_without_state(runner):
    result, calls = recover_bucket(runner, STATE_HEAD_RESULT='missing')
    assert result.returncode == 0
    assert not any(c[0] == 'terraform' for c in calls())
