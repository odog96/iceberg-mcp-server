"""Chat app model adapters, with fake provider clients (no network, no credentials)."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("openai")
pytest.importorskip("boto3")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "chat_app"))
import llm  # noqa: E402

TOOL = SimpleNamespace(name="get_schema", description="List tables", inputSchema={"type": "object", "properties": {}})


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("LLM_PROVIDER", "CAI_INFERENCE_BASE_URL", "CAI_INFERENCE_MODEL", "BEDROCK_MODEL_ID"):
        monkeypatch.delenv(k, raising=False)


def test_nothing_configured_is_explained():
    with pytest.raises(RuntimeError, match="CAI_INFERENCE_BASE_URL.*BEDROCK_MODEL_ID"):
        llm.from_env()


def test_provider_inferred_from_settings(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "m")
    assert isinstance(llm.from_env(), llm.Bedrock)


def test_both_configured_needs_choice(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "m")
    monkeypatch.setenv("CAI_INFERENCE_BASE_URL", "http://x/v1")
    with pytest.raises(RuntimeError, match="LLM_PROVIDER"):
        llm.from_env()
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    assert isinstance(llm.from_env(), llm.Bedrock)


def test_missing_settings_are_named(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "cai")
    with pytest.raises(RuntimeError, match="CAI_INFERENCE_BASE_URL"):
        llm.from_env()


def test_bedrock_tool_call_and_results(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "m")
    model = llm.Bedrock()
    sent = {}

    def converse(**kw):
        sent.update(kw)
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": "checking"},
                        {"toolUse": {"toolUseId": "t1", "name": "get_schema", "input": {}}},
                    ],
                }
            }
        }

    model.client = SimpleNamespace(converse=converse)
    messages = model.start([{"role": "user", "content": "tables?"}])
    reply = model.chat("sys", messages, [TOOL])
    assert sent["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"] == TOOL.inputSchema
    assert reply.text == "checking" and reply.tool_calls[0].name == "get_schema"
    model.add_tool_results(messages, [(reply.tool_calls[0], '["movies"]')])
    assert messages[-1] == {
        "role": "user",
        "content": [{"toolResult": {"toolUseId": "t1", "content": [{"text": '["movies"]'}]}}],
    }


def test_cai_inference_tool_call_and_results(monkeypatch):
    monkeypatch.setenv("CAI_INFERENCE_BASE_URL", "http://x/v1")
    monkeypatch.setenv("CAI_INFERENCE_MODEL", "m")
    monkeypatch.setenv("CAI_INFERENCE_API_KEY", "k")
    model = llm.CaiInference()
    sent = {}
    msg = SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(id="c1", function=SimpleNamespace(name="get_schema", arguments="{}"))],
        model_dump=lambda exclude_none: {"role": "assistant", "tool_calls": ["..."]},
    )

    def create(**kw):
        sent.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    completions = SimpleNamespace(create=create)
    model.client = SimpleNamespace(
        with_options=lambda api_key: SimpleNamespace(chat=SimpleNamespace(completions=completions))
    )
    messages = model.start([{"role": "user", "content": "tables?"}])
    reply = model.chat("sys", messages, [TOOL])
    assert sent["messages"][0] == {"role": "system", "content": "sys"}
    assert sent["tools"][0]["function"]["parameters"] == TOOL.inputSchema
    assert reply.tool_calls[0].name == "get_schema" and reply.tool_calls[0].arguments == {}
    model.add_tool_results(messages, [(reply.tool_calls[0], json.dumps(["movies"]))])
    assert messages[-1] == {"role": "tool", "tool_call_id": "c1", "content": '["movies"]'}


def test_cai_inference_reads_cdp_token_when_no_key(monkeypatch, tmp_path):
    monkeypatch.delenv("CAI_INFERENCE_API_KEY", raising=False)
    jwt_file = tmp_path / "jwt"
    jwt_file.write_text(json.dumps({"access_token": "cdp-token"}))
    real_open = open
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: real_open(jwt_file if p == "/tmp/jwt" else p, *a, **k))
    assert llm.CaiInference._api_key() == "cdp-token"
