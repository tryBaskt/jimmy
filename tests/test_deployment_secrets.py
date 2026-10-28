import boto3
import pytest
from botocore.stub import Stubber
from unittest.mock import Mock

from scripts import deployment_secrets as deployment


TOPIC = "arn:aws:sns:us-east-1:123456789012:jimmy-dev-alerts"


def secret_arn(name):
    return f"arn:aws:secretsmanager:us-east-1:123456789012:secret:{name}-a1B2c3"


def sdk_client(service):
    return boto3.client(service, region_name="us-east-1", aws_access_key_id="dummy", aws_secret_access_key="dummy")


def test_secrets_go_directly_to_sdk_and_only_references_are_emitted(monkeypatch, capsys):
    key = 'dummy-test-key-with-"quotes"-and-$(text)'
    email = "private-test@example.com"
    monkeypatch.setenv("BIGDATA_API_KEY", key)
    monkeypatch.setenv("ALERT_EMAIL", email)
    client = sdk_client("secretsmanager")
    monkeypatch.setattr(deployment, "aws_client", lambda service: client)
    with Stubber(client) as stub:
        stub.add_response("create_secret", {"ARN": secret_arn("jimmy-dev-bigdata-api-key")},
                          {"Name": "jimmy-dev-bigdata-api-key", "SecretString": key})
        stub.add_response("create_secret", {"ARN": secret_arn("jimmy-dev-alert-email")},
                          {"Name": "jimmy-dev-alert-email", "SecretString": email})
        deployment.sync("dev")
        stub.assert_no_pending_responses()
    output = capsys.readouterr()
    assert key not in output.out + output.err and email not in output.out + output.err
    assert output.out.splitlines() == [
        f"BIGDATA_SECRET_ARN={secret_arn('jimmy-dev-bigdata-api-key')}",
        f"ALERT_EMAIL_SECRET_ARN={secret_arn('jimmy-dev-alert-email')}",
    ]


def test_existing_secret_rotates_without_reading_value():
    name = "jimmy-prod-bigdata-api-key"
    client = sdk_client("secretsmanager")
    with Stubber(client) as stub:
        stub.add_client_error("create_secret", service_error_code="ResourceExistsException",
                              expected_params={"Name": name, "SecretString": "replacement-key"})
        stub.add_response("put_secret_value", {"ARN": secret_arn(name)},
                          {"SecretId": name, "SecretString": "replacement-key"})
        assert deployment.upsert_secret(client, name, "replacement-key") == secret_arn(name)
        stub.assert_no_pending_responses()


def test_failed_write_does_not_expose_error_output_or_emit_partial_references(monkeypatch, capsys):
    monkeypatch.setenv("DEPLOY_ENV", "dev")
    monkeypatch.setenv("BIGDATA_API_KEY", "dummy-key")
    monkeypatch.setenv("ALERT_EMAIL", "private-test@example.com")
    monkeypatch.setattr(deployment.sys, "argv", ["deployment_secrets.py", "sync"])
    client = sdk_client("secretsmanager")
    monkeypatch.setattr(deployment, "aws_client", lambda service: client)
    with Stubber(client) as stub:
        stub.add_response("create_secret", {"ARN": secret_arn("jimmy-dev-bigdata-api-key")},
                          {"Name": "jimmy-dev-bigdata-api-key", "SecretString": "dummy-key"})
        stub.add_client_error("create_secret", service_error_code="AccessDeniedException",
                              service_message="private-test@example.com dummy-key",
                              expected_params={"Name": "jimmy-dev-alert-email", "SecretString": "private-test@example.com"})
        assert deployment.main() == 1
        stub.assert_no_pending_responses()
    output = capsys.readouterr()
    assert output.out == ""
    assert "dummy-key" not in output.err and "private-test@example.com" not in output.err
    assert "Details suppressed" in output.err


def test_secret_arn_cannot_inject_github_environment_lines():
    client = sdk_client("secretsmanager")
    with Stubber(client) as stub:
        stub.add_response("create_secret", {"ARN": "not-an-arn\nOTHER_SETTING=bad"},
                          {"Name": "jimmy-dev-alert-email", "SecretString": "dummy-test@example.com"})
        with pytest.raises(deployment.DeploymentError, match="invalid secret ARN"):
            deployment.upsert_secret(client, "jimmy-dev-alert-email", "dummy-test@example.com")


def test_empty_optional_secrets_do_not_erase_stored_values(monkeypatch, capsys):
    monkeypatch.delenv("BIGDATA_API_KEY", raising=False)
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    client = Mock()
    monkeypatch.setattr(deployment, "aws_client", lambda service: client)
    deployment.sync("dev")
    deployment.configure_alerts("dev", TOPIC)
    assert client.mock_calls == []
    assert "BIGDATA_SECRET_ARN=\n" in capsys.readouterr().out


@pytest.mark.parametrize("confirmed", [True, False])
def test_alert_configuration_paginates_and_preserves_other_protocols(monkeypatch, capsys, confirmed):
    monkeypatch.setenv("ALERT_EMAIL", "current-test@example.com")
    client = sdk_client("sns")
    monkeypatch.setattr(deployment, "aws_client", lambda service: client)
    with Stubber(client) as stub:
        stub.add_response("list_subscriptions_by_topic", {"Subscriptions": [
            {"Protocol": "email", "Endpoint": "current-test@example.com",
             "SubscriptionArn": TOPIC + ":current" if confirmed else "PendingConfirmation"},
        ], "NextToken": "next-page"}, {"TopicArn": TOPIC})
        stub.add_response("list_subscriptions_by_topic", {"Subscriptions": [
            {"Protocol": "email", "Endpoint": "old-test@example.com", "SubscriptionArn": TOPIC + ":old"},
            {"Protocol": "email", "Endpoint": "pending-test@example.com", "SubscriptionArn": "PendingConfirmation"},
            {"Protocol": "sqs", "Endpoint": "queue", "SubscriptionArn": TOPIC + ":queue"},
        ]}, {"TopicArn": TOPIC, "NextToken": "next-page"})
        if not confirmed:
            stub.add_response("subscribe", {}, {"TopicArn": TOPIC, "Protocol": "email", "Endpoint": "current-test@example.com"})
        stub.add_response("unsubscribe", {}, {"SubscriptionArn": TOPIC + ":old"})
        deployment.configure_alerts("dev", TOPIC)
        stub.assert_no_pending_responses()
    output = capsys.readouterr()
    assert "@example.com" not in output.out + output.err


def test_alert_setup_rejects_other_environment(monkeypatch):
    client_factory = Mock()
    monkeypatch.setattr(deployment, "aws_client", client_factory)
    with pytest.raises(deployment.DeploymentError, match="does not match"):
        deployment.configure_alerts("prod", TOPIC)
    client_factory.assert_not_called()
