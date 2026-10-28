"""Write GitHub-provided secrets without exposing values in arguments, logs or state.

It never retrieves stored secret values. Private values are passed directly from
step-scoped environment variables to the AWS SDK, never through shell arguments.
"""

import argparse
import os
import re
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class DeploymentError(Exception):
    pass


def aws_client(service: str):
    return boto3.client(
        service, region_name=os.environ.get("AWS_REGION", "us-east-1"),
        config=Config(retries={"mode": "standard", "total_max_attempts": 4}, connect_timeout=5, read_timeout=30),
    )


def upsert_secret(client, name: str, value: str) -> str:
    try:
        result = client.create_secret(Name=name, SecretString=value)
    except client.exceptions.ResourceExistsException:
        result = client.put_secret_value(SecretId=name, SecretString=value)
    arn = result.get("ARN", "")
    # Only validated ARN metadata may enter GITHUB_ENV. Never emit SecretString.
    pattern = rf"arn:aws[a-z-]*:secretsmanager:[a-z0-9-]+:[0-9]{{12}}:secret:{re.escape(name)}-[A-Za-z0-9]{{6}}"
    if not isinstance(arn, str) or not re.fullmatch(pattern, arn):
        raise DeploymentError("Secrets Manager returned an invalid secret ARN; output suppressed.")
    return arn


def sync(environment: str) -> None:
    references = {}
    client = aws_client("secretsmanager")
    for source, purpose, output in [
        ("BIGDATA_API_KEY", "bigdata-api-key", "BIGDATA_SECRET_ARN"),
        ("ALERT_EMAIL", "alert-email", "ALERT_EMAIL_SECRET_ARN"),
    ]:
        value = os.environ.get(source, "")
        references[output] = upsert_secret(client, f"jimmy-{environment}-{purpose}", value) if value else ""
    # Emit only after both writes succeed. Values are supplied by step-scoped env,
    # never interpolated into shell source or passed as command-line arguments.
    for key, arn in references.items():
        print(f"{key}={arn}")


def configure_alerts(environment: str, topic_arn: str) -> None:
    if not re.fullmatch(rf"arn:aws[a-z-]*:sns:[a-z0-9-]+:[0-9]{{12}}:jimmy-{environment}-alerts", topic_arn):
        raise DeploymentError("Alert topic does not match the deployment environment.")
    email = os.environ.get("ALERT_EMAIL", "")
    if not email:
        print("No alert email supplied; existing secret and subscriptions retained.")
        return
    client = aws_client("sns")
    subscriptions = [
        item for page in client.get_paginator("list_subscriptions_by_topic").paginate(TopicArn=topic_arn)
        for item in page.get("Subscriptions", [])
    ]
    confirmed = any(
        item.get("Protocol") == "email" and item.get("Endpoint") == email
        and item.get("SubscriptionArn", "").startswith(topic_arn + ":")
        for item in subscriptions
    )
    if not confirmed:
        client.subscribe(TopicArn=topic_arn, Protocol="email", Endpoint=email)
        print("Alert subscription requested. Confirm the AWS SNS email to enable delivery.")
    else:
        print("Alert subscription already confirmed.")
    # This dedicated topic has one deployment-managed email recipient. Remove
    # confirmed stale addresses after replacing it. Pending requests cannot deliver.
    for item in subscriptions:
        arn = item.get("SubscriptionArn", "")
        if item.get("Protocol") == "email" and item.get("Endpoint") != email and arn.startswith(topic_arn + ":"):
            client.unsubscribe(SubscriptionArn=arn)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["sync", "alerts"])
    parser.add_argument("--topic-arn", default="")
    args = parser.parse_args()
    environment = os.environ.get("DEPLOY_ENV", "")
    try:
        if environment not in {"dev", "prod"}:
            raise DeploymentError("DEPLOY_ENV must be dev or prod.")
        if args.command == "sync":
            sync(environment)
        else:
            configure_alerts(environment, args.topic_arn)
    except DeploymentError as error:
        # Never print raw AWS responses, request payloads or tracebacks.
        print(str(error), file=sys.stderr)
        return 1
    except (ClientError, BotoCoreError):
        print("AWS secret/alert configuration failed; inspect AWS permissions and configuration. Details suppressed to protect private values.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
