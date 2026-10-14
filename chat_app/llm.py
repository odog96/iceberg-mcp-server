"""Chat model providers with tool calling: Cloudera AI Inference (OpenAI-compatible) or AWS Bedrock.

Configured by environment variables:

    LLM_PROVIDER              cai | bedrock. Optional when only one provider is configured.

    cai (Cloudera AI Inference service):
    CAI_INFERENCE_BASE_URL    the endpoint's OpenAI-compatible URL, as shown on its Model Endpoints
                              page, e.g. https://<domain>/namespaces/serving-default/endpoints/<name>/openai/v1
    CAI_INFERENCE_MODEL       optional; default is the model the endpoint lists (it serves one)
    CAI_INFERENCE_API_KEY     optional; default is the CDP token CAI writes to /tmp/jwt (read on
                              every call, because it expires)

    bedrock:
    BEDROCK_MODEL_ID          model or inference-profile id, e.g. us.anthropic.claude-sonnet-4-...
    AWS_REGION                e.g. us-east-1
    and either AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (+ AWS_SESSION_TOKEN),
    or AWS_BEARER_TOKEN_BEDROCK (a Bedrock API key). boto3 picks up both on its own.

Both providers expose chat(system, messages, tools) -> Reply, where messages is the provider's own
message list (built by start()/add_tool_results()), so one tool-calling loop drives either.
"""

import json
import os
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


def _require(*names):
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        raise RuntimeError(f"Missing settings: {', '.join(missing)}")


class CaiInference:
    """Cloudera AI Inference service, through its OpenAI-compatible API."""

    def __init__(self):
        _require("CAI_INFERENCE_BASE_URL")
        from openai import OpenAI

        self.client = OpenAI(base_url=os.environ["CAI_INFERENCE_BASE_URL"].rstrip("/"), api_key="unused")
        self.model = os.getenv("CAI_INFERENCE_MODEL") or self._discover_model()

    def _discover_model(self):
        models = [m.id for m in self.client.with_options(api_key=self._api_key()).models.list()]
        if not models:
            raise RuntimeError("The endpoint lists no model; set CAI_INFERENCE_MODEL")
        return models[0]

    def label(self):
        return f"Cloudera AI Inference: {self.model}"

    @staticmethod
    def _api_key():
        if os.getenv("CAI_INFERENCE_API_KEY"):
            return os.environ["CAI_INFERENCE_API_KEY"]
        with open("/tmp/jwt") as f:
            return json.load(f)["access_token"]

    def start(self, history):
        return [{"role": m["role"], "content": m["content"]} for m in history]

    def chat(self, system, messages, tools):
        resp = self.client.with_options(api_key=self._api_key()).chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, *messages],
            tools=[
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description or "", "parameters": t.inputSchema},
                }
                for t in tools
            ],
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        calls = [
            ToolCall(c.id, c.function.name, json.loads(c.function.arguments or "{}")) for c in msg.tool_calls or []
        ]
        return Reply(msg.content or "", calls)

    def add_tool_results(self, messages, results):
        for call, output in results:
            messages.append({"role": "tool", "tool_call_id": call.id, "content": output})


class Bedrock:
    """AWS Bedrock, through the Converse API."""

    def __init__(self):
        _require("BEDROCK_MODEL_ID")
        import boto3

        self.model = os.environ["BEDROCK_MODEL_ID"]
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        self.client = boto3.client("bedrock-runtime", region_name=region)

    def label(self):
        return f"AWS Bedrock: {self.model}"

    def start(self, history):
        return [{"role": m["role"], "content": [{"text": m["content"]}]} for m in history]

    def chat(self, system, messages, tools):
        resp = self.client.converse(
            modelId=self.model,
            system=[{"text": system}],
            messages=messages,
            toolConfig={
                "tools": [
                    {
                        "toolSpec": {
                            "name": t.name,
                            "description": t.description or t.name,
                            "inputSchema": {"json": t.inputSchema},
                        }
                    }
                    for t in tools
                ]
            },
        )
        msg = resp["output"]["message"]
        messages.append(msg)
        text = "".join(b["text"] for b in msg["content"] if "text" in b)
        calls = [
            ToolCall(b["toolUse"]["toolUseId"], b["toolUse"]["name"], b["toolUse"]["input"])
            for b in msg["content"]
            if "toolUse" in b
        ]
        return Reply(text, calls)

    def add_tool_results(self, messages, results):
        messages.append(
            {
                "role": "user",
                "content": [
                    {"toolResult": {"toolUseId": call.id, "content": [{"text": output}]}} for call, output in results
                ],
            }
        )


def from_env():
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if not provider:
        configured = [
            name for name, var in (("cai", "CAI_INFERENCE_BASE_URL"), ("bedrock", "BEDROCK_MODEL_ID")) if os.getenv(var)
        ]
        provider = configured[0] if len(configured) == 1 else ""
    if provider == "cai":
        return CaiInference()
    if provider == "bedrock":
        return Bedrock()
    raise RuntimeError(
        "No model configured: set CAI_INFERENCE_BASE_URL (Cloudera AI Inference) or BEDROCK_MODEL_ID (AWS Bedrock). "
        "If both are set, choose with LLM_PROVIDER=cai or bedrock"
    )
