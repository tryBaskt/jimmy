"""Release individual components from a commit-bound, non-secret Terraform contract."""

import argparse
import copy
import json
import os
from pathlib import Path
import re
import sys
import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class ReleaseError(Exception):
    pass


def context():
    env = os.environ.get("DEPLOY_ENV", "")
    commit = os.environ.get("GITHUB_SHA", "")
    if env not in {"dev", "prod"} or not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ReleaseError("A valid deployment environment and commit are required.")
    return {"environment": env, "commit": commit}


def client(service):
    return boto3.client(service, region_name=os.environ["AWS_REGION"], config=Config(
        retries={"mode": "standard", "total_max_attempts": 4}, connect_timeout=5, read_timeout=30,
    ))


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise ReleaseError("Deployment metadata is missing or invalid.") from None


def validate_image(image, target):
    expected = (
        f"{os.environ['AWS_ACCOUNT_ID']}.dkr.ecr.{os.environ['AWS_REGION']}.amazonaws.com/"
        f"jimmy-{context()['environment']}-{target}@sha256:"
    )
    if not isinstance(image, str) or not re.fullmatch(re.escape(expected) + r"[a-f0-9]{64}", image):
        raise ReleaseError("Image must be pinned to a digest in the matching environment repository.")
    return image


def image_manifest(target):
    data = read_json(f".deployment/{target}-image.json")
    if any(data.get(k) != v for k, v in context().items()):
        raise ReleaseError("Image artifact belongs to a different commit or environment.")
    return validate_image(data.get("image"), target)


def snapshot():
    data = read_json(".deployment/infrastructure.json")
    if data.get("_release") != context():
        raise ReleaseError("Infrastructure artifact belongs to a different commit or environment.")
    return data


def contract():
    data = snapshot()["release_contract"]["value"]
    if data["environment"] != context()["environment"]:
        raise ReleaseError("Component contract environment does not match.")
    validate_image(data["api_image"], "api")
    validate_image(data["worker_image"], "worker")
    return data


def receipt(name, values):
    Path(".deployment").mkdir(exist_ok=True)
    Path(f".deployment/{name}-receipt.json").write_text(json.dumps({**context(), **values}))


def release_lambda(group, service):
    data = contract()
    name = data[f"{group}_functions"].get(service)
    if name != f"jimmy-{context()['environment']}-{service}":
        raise ReleaseError("Function does not match the component contract.")
    api = client("lambda")
    wait = {"FunctionName": name, "WaiterConfig": {"Delay": 2, "MaxAttempts": 150}}
    api.get_waiter("function_active_v2").wait(**wait)
    api.get_waiter("function_updated_v2").wait(**wait)
    before = api.get_function(FunctionName=name)
    image = data["api_image"]
    previous = before["Code"].get("ResolvedImageUri")
    if previous != image:
        api.update_function_code(FunctionName=name, ImageUri=image, RevisionId=before["Configuration"]["RevisionId"])
        api.get_waiter("function_updated_v2").wait(**wait)
    after = api.get_function(FunctionName=name)
    if after["Code"].get("ResolvedImageUri") != image:
        raise ReleaseError("Lambda did not resolve to the requested image digest.")
    receipt(service, {"function": name, "image": image, "previous_image": previous})
    print(f"Released {group} component: {service}")


def release_task(kind):
    data = contract()
    template = copy.deepcopy(data["task_templates"][kind])
    family = f"jimmy-{context()['environment']}-{kind}"
    if template["family"] != family or len(template["containerDefinitions"]) != 1:
        raise ReleaseError("Task template does not match the component contract.")
    template["containerDefinitions"][0]["image"] = data["worker_image"]
    api = client("ecs")
    definition = api.register_task_definition(**template)["taskDefinition"]
    arn = definition["taskDefinitionArn"]
    expected = f"arn:aws:ecs:{os.environ['AWS_REGION']}:{os.environ['AWS_ACCOUNT_ID']}:task-definition/{family}:"
    if not arn.startswith(expected) or not arn[len(expected):].isdigit():
        raise ReleaseError("Registered task ARN does not match the requested family.")
    receipt(kind, {"task_arn": arn, "image": data["worker_image"]})
    print(f"Registered isolated task component: {kind}")


def release_workflow():
    data = contract()
    definitions = {}
    for kind in ["worker", "runner"]:
        item = read_json(f".deployment/{kind}-receipt.json")
        if any(item.get(k) != v for k, v in context().items()) or item.get("image") != data["worker_image"]:
            raise ReleaseError("Task receipts must match the workflow commit, environment and image.")
        arn = item.get("task_arn", "")
        expected = f"arn:aws:ecs:{os.environ['AWS_REGION']}:{os.environ['AWS_ACCOUNT_ID']}:task-definition/jimmy-{context()['environment']}-{kind}:"
        if not arn.startswith(expected) or not arn[len(expected):].isdigit():
            raise ReleaseError("Task receipt does not contain a pinned matching revision.")
        definitions[kind] = arn
    graph = copy.deepcopy(data["workflow_definition"])
    for stage, state in graph["States"].items():
        if state.get("Resource") == "arn:aws:states:::ecs:runTask.sync":
            state["Parameters"]["TaskDefinition"] = definitions["runner" if stage in {"run", "extension_run"} else "worker"]
    api = client("stepfunctions")
    encoded = json.dumps(graph, sort_keys=True)
    # Only publish after both isolated task jobs finish. Standard executions already
    # in progress retain their existing definition with pinned task revisions.
    api.update_state_machine(stateMachineArn=data["workflow_arn"], definition=encoded)
    for attempt in range(20):
        actual = api.describe_state_machine(stateMachineArn=data["workflow_arn"])
        if json.loads(actual["definition"]) == graph:
            break
        if attempt < 19:
            time.sleep(2)
    else:
        raise ReleaseError("Workflow readback does not match the desired definition.")
    receipt("workflow", {"workflow_arn": data["workflow_arn"], "tasks": definitions})
    print("Released research workflow with pinned worker and runner revisions.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    images = commands.add_parser("image")
    images.add_argument("target", choices=["api", "worker"])
    outputs = commands.add_parser("output")
    outputs.add_argument("key", choices=["frontend_bucket", "distribution_id", "frontend_url", "api_url", "alerts_topic_arn"])
    lambdas = commands.add_parser("lambda")
    lambdas.add_argument("group", choices=["api", "realtime"])
    lambdas.add_argument("service")
    tasks = commands.add_parser("task")
    tasks.add_argument("kind", choices=["worker", "runner"])
    commands.add_parser("workflow")
    args = parser.parse_args()
    try:
        if args.command == "image":
            print(image_manifest(args.target))
        elif args.command == "output":
            print(snapshot()[args.key]["value"])
        elif args.command == "lambda":
            release_lambda(args.group, args.service)
        elif args.command == "task":
            release_task(args.kind)
        else:
            release_workflow()
    except (ReleaseError, KeyError, TypeError) as error:
        print(str(error) if isinstance(error, ReleaseError) else "Invalid release contract.", file=sys.stderr)
        return 1
    except (ClientError, BotoCoreError):
        print("AWS component release failed. Inspect AWS permissions and service events.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
