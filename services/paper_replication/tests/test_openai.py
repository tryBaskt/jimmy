"""Exercise the real SDK against an offline transport; never call a model."""
import json

import httpx
import pytest
from openai import OpenAI

from agents import ResearchAgents
from models import DatasetSpec
from settings import configure_openai


def response(text, status="completed", refusal=False):
    content = {"type": "refusal", "refusal": "Unable to comply"} if refusal else {
        "type": "output_text", "text": text, "annotations": []}
    return {"id": "resp_test", "object": "response", "created_at": 0,
            "model": "gpt-6.1-sol", "status": status, "output": [{"type": "message",
                "id": "msg_test", "role": "assistant", "status": "completed", "content": [content]}]}


def agents_with_responses(tmp_path, outputs):
    calls = []
    def handle(request):
        assert request.url.path == "/v1/responses"
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=outputs[min(len(calls) - 1, len(outputs) - 1)])
    client = OpenAI(api_key="offline-test-key", max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    return ResearchAgents("gpt-6.1-sol", "", tmp_path, client=client), calls


def test_structured_output_validates_and_retries_with_explicit_budget(tmp_path):
    good = {"name": "Prices", "source": "yfinance", "symbols": ["AAPL"], "start": "2000-01-01",
            "end": "2020-01-01", "csv_url": None, "rationale": "Test", "known_differences": []}
    agents, calls = agents_with_responses(tmp_path, [response("{}"), response(json.dumps(good))])
    assert agents.structured(DatasetSpec, "Test", {}, "plan").symbols == ["AAPL"]
    assert len(calls) == 2
    for call in calls:
        assert call["max_output_tokens"] == 12000
        assert call["store"] is False
        assert call["text"]["format"]["type"] == "json_schema"
        assert call["text"]["format"]["strict"] is True
    assert "Correct the previous invalid output" in calls[1]["instructions"]
    assert (tmp_path / "plan_2.json").exists()


@pytest.mark.parametrize("output", [response("", refusal=True), response("{}", status="incomplete")])
def test_unusable_structured_output_is_bounded(tmp_path, output):
    agents, calls = agents_with_responses(tmp_path, [output])
    with pytest.raises(ValueError, match="did not produce valid"):
        agents.structured(DatasetSpec, "Test", {}, "plan")
    assert len(calls) == 2


def test_assessment_uses_openai_response_text(tmp_path):
    agents, calls = agents_with_responses(tmp_path, [response("Evidence assessment")])
    assert agents.interpret({"results": []}) == "Evidence assessment"
    assert calls[0]["max_output_tokens"] == 7000
    assert calls[0]["store"] is False
    assert (tmp_path / "report_response.json").exists()


def test_incomplete_assessment_fails(tmp_path):
    agents, _ = agents_with_responses(tmp_path, [response("Partial", status="incomplete")])
    with pytest.raises(RuntimeError, match="did not complete"):
        agents.interpret({})


@pytest.fixture
def isolated_env(monkeypatch):
    for key in ["OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_API_KEY_SECRET_ARN"]:
        monkeypatch.delenv(key, raising=False)


def test_runtime_environment_key_and_model_are_preserved(isolated_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "injected-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "override-model")
    assert configure_openai() == "override-model"


def test_dotenv_is_not_loaded_by_runtime(isolated_env, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=unused-test-key\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="Configure OPENAI_API_KEY_SECRET_ARN"):
        configure_openai()


@pytest.mark.parametrize("value", ["cloud-test-key", '{"OPENAI_API_KEY":"cloud-test-key"}'])
def test_cloud_secret_is_loaded_only_at_runtime(isolated_env, monkeypatch, value):
    import os
    from types import SimpleNamespace
    arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:openai-test"
    monkeypatch.setenv("OPENAI_API_KEY_SECRET_ARN", arn)
    calls = []
    def get_secret_value(**kwargs):
        calls.append(kwargs)
        return {"SecretString": value}
    assert configure_openai(SimpleNamespace(get_secret_value=get_secret_value)) == "gpt-6.1-sol"
    assert calls == [{"SecretId": arn}]
    assert os.environ["OPENAI_API_KEY"] == "cloud-test-key"


def test_invalid_cloud_secret_fails_without_echoing_secret(isolated_env, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("OPENAI_API_KEY_SECRET_ARN", "test-arn")
    client = SimpleNamespace(get_secret_value=lambda **kwargs: {"SecretString": '{"wrong":"private-value"}'})
    with pytest.raises(ValueError, match="nonempty key") as error:
        configure_openai(client)
    assert "private-value" not in str(error.value)
