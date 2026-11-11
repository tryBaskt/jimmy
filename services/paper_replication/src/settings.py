"""OpenAI runtime configuration. Secret values never enter deployment outputs."""
import json
import os


def configure_openai(secrets_client=None) -> str:
    if not os.getenv("OPENAI_API_KEY"):
        arn = os.getenv("OPENAI_API_KEY_SECRET_ARN")
        if not arn:
            raise ValueError("Configure OPENAI_API_KEY_SECRET_ARN for the ECS worker")
        # Executed only inside the deployed runtime, never by deployment tooling.
        if secrets_client is None:
            import boto3
            from botocore.config import Config
            secrets_client = boto3.client("secretsmanager", region_name=arn.split(":")[3],
                config=Config(connect_timeout=10, read_timeout=30,
                              retries={"total_max_attempts": 3, "mode": "standard"}))
        value = secrets_client.get_secret_value(SecretId=arn)["SecretString"]
        if value.lstrip().startswith("{"):
            value = json.loads(value).get("OPENAI_API_KEY")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("OpenAI secret must contain a nonempty key or an OPENAI_API_KEY JSON field")
        os.environ["OPENAI_API_KEY"] = value.strip()
    return os.getenv("OPENAI_MODEL", "gpt-6.1-sol")
