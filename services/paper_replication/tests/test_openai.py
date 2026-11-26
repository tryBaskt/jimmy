"""Exercise the real SDK against an offline transport; never call a model."""
import json

import httpx
import pytest

from harness import workflow
from harness.models import DatasetSpec
from settings import configure_openai


def response(text, status="completed", refusal=False):
    content = {"type": "refusal", "refusal": "Unable to comply"} if refusal else {
        "type": "output_text", "text": text, "annotations": []}
    return {"id": "resp_test", "object": "response", "created_at": 0,
            "model": "gpt-6.1-sol", "status": status, "output": [{"type": "message",
                "id": "msg_test", "role": "assistant", "status": "completed", "content": [content]}]}


def model_with_responses(outputs, model="gpt-6.1-sol"):
    calls = []
    def handle(request):
        assert request.url.path == "/v1/responses"
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=outputs[min(len(calls) - 1, len(outputs) - 1)])
    client = httpx.Client(transport=httpx.MockTransport(handle))
    return workflow.chat_model(model, max_tokens=12000, http_client=client), calls


@pytest.mark.parametrize("output", [response("", refusal=True), response("{}", status="incomplete")])
def test_pi_rejects_refused_or_incomplete_response(tmp_path, monkeypatch, output):
    model, calls = model_with_responses([output])
    monkeypatch.setattr(workflow, "read_paper", lambda path: ["Winners outperform losers"])
    runtime = workflow.Runtime(model, None, tmp_path, "2026-10-29", {})
    with pytest.raises(RuntimeError, match="incomplete or invalid"):
        workflow.run_research(runtime, tmp_path / "paper.pdf")
    assert len(calls) == 1
    assert calls[0]["max_output_tokens"] == 12000
    assert calls[0]["store"] is False


def test_research_assistant_calls_only_its_assigned_tool(tmp_path, monkeypatch):
    tool_call = {"type": "function_call", "name": "collect_data", "arguments": "{}",
                 "call_id": "call_test", "id": "fc_test", "status": "completed"}
    output = response("unused") | {"model": "gpt-5-mini", "output": [tool_call]}
    model, calls = model_with_responses([output], "gpt-5-mini")
    fetched = []

    def acquire(spec, folder, local_csv):
        fetched.append((spec.symbols, folder, local_csv))
        return {"verified_available": True}

    monkeypatch.setattr(workflow, "acquire", acquire)
    dataset = DatasetSpec(name="Prices", source="yfinance", symbols=["AAPL"],
                          start="2000-01-01", end="2020-01-01", csv_url=None,
                          rationale="Test", known_differences=[])
    assert workflow.collect_candidate(model, dataset, tmp_path) == {"verified_available": True}
    assert fetched == [(["AAPL"], tmp_path, None)]
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-5-mini"
    assert calls[0]["tool_choice"] == {"type": "function", "name": "collect_data"}
    assert [tool["name"] for tool in calls[0]["tools"]] == ["collect_data"]
    assert calls[0]["store"] is False


@pytest.fixture(autouse=True)
def offline_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.delenv("RESEARCH_ASSISTANT_MODEL", raising=False)


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
