"""Amazon Bedrock adapter for one validated, structured model response."""

from __future__ import annotations

from typing import Any

from botocore.config import Config


class BedrockStructuredClient:
    def __init__(self, *, session: Any, model_id: str) -> None:
        if not model_id:
            raise ValueError("A Bedrock model ID is required")
        self.model_id = model_id
        self._client = session.client(
            "bedrock-runtime",
            config=Config(retries={"mode": "adaptive", "max_attempts": 5}),
        )

    def request(
        self, *, instructions: str, prompt: str, tool: dict, max_tokens: int
    ) -> dict:
        """Require exactly one invocation of the specified structured-output tool."""
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError("Model instructions are required")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("A model prompt is required")
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError("max_tokens must be a positive integer")
        try:
            name = tool["toolSpec"]["name"]
        except (KeyError, TypeError) as error:
            raise ValueError("A valid tool specification is required") from error
        if not isinstance(name, str) or not name:
            raise ValueError("A valid tool name is required")

        response = self._client.converse(
            modelId=self.model_id,
            system=[{"text": instructions}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens},
            toolConfig={"tools": [tool], "toolChoice": {"auto": {}}},
        )
        content = response.get("output", {}).get("message", {}).get("content", [])
        uses = [block["toolUse"] for block in content if isinstance(block, dict) and "toolUse" in block]
        if (
            response.get("stopReason") != "tool_use"
            or len(uses) != 1
            or not isinstance(uses[0], dict)
            or uses[0].get("name") != name
            or not isinstance(uses[0].get("input"), dict)
        ):
            raise ValueError(f"The model did not return one valid {name} result")
        return uses[0]["input"]
