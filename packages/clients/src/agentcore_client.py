import json
import os
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException


@lru_cache
def client():
    return boto3.client("bedrock-agentcore", config=Config(
        connect_timeout=10, read_timeout=60,
        retries={"total_max_attempts": 1, "mode": "standard"},
    ))


def invoke(run_id, owner, action="snapshot", **arguments):
    payload = {"run_id": run_id, "owner": owner, "action": action, **arguments}
    try:
        arn = os.getenv("AGENTCORE_RUNTIME_ARN")
        if not arn:
            raise HTTPException(503, "AgentCore runtime is not configured")
        response = client().invoke_agent_runtime(
            agentRuntimeArn=arn, runtimeSessionId=run_id,
            qualifier=os.getenv("AGENTCORE_ENDPOINT", "DEFAULT"),
            contentType="application/json", accept="application/json",
            payload=json.dumps(payload).encode(),
        )
        with response["response"] as body:
            result = json.loads(body.read())
    except (BotoCoreError, ClientError) as error:
        raise HTTPException(502, "Workflow runtime is unavailable. Retry with the same run ID.") from error
    if "status_code" in result:
        raise HTTPException(result["status_code"], result["error"])
    return result
