"""Per-workspace AI provider settings: validation, secret masking, LLM factory."""

import pytest
from fastapi.testclient import TestClient

from modelling.llm import llm_for_settings


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.setenv("PROCESSIQ_DISABLE_SCHEDULER", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def auth(client):
    key = client.post("/api/workspaces", json={"name": "AI Test"}).json()["api_key"]
    return {"X-API-Key": key}


def test_defaults_masking_and_roundtrip(client, auth):
    initial = client.get("/api/ai-provider", headers=auth).json()
    assert initial["provider"] == "anthropic"
    assert initial["model"]  # server default model
    assert initial["active"] is False and initial["source"] == "none"
    assert initial["sdk"]["anthropic"] is True

    # unknown providers rejected
    assert client.put("/api/ai-provider", json={"provider": "openai"},
                      headers=auth).status_code == 400

    # configure Anthropic with a key: masked on read, never echoed back
    saved = client.put("/api/ai-provider", json={
        "provider": "anthropic", "model": "claude-sonnet-5",
        "anthropic_key": "sk-ant-secret",
    }, headers=auth).json()
    assert saved["has_anthropic_key"] is True
    assert "sk-ant" not in str(saved)
    assert saved["active"] is True and saved["source"] == "workspace"

    # switching provider keeps the stored key (None = untouched), "" clears it
    vertex = client.put("/api/ai-provider", json={
        "provider": "vertex", "model": "claude-sonnet-5",
        "vertex_project": "my-gcp-project", "vertex_region": "us-east5",
    }, headers=auth).json()
    assert vertex["has_anthropic_key"] is True
    assert vertex["vertex_project"] == "my-gcp-project"
    cleared = client.put("/api/ai-provider", json={
        "provider": "vertex", "anthropic_key": "",
    }, headers=auth).json()
    assert cleared["has_anthropic_key"] is False

    bedrock = client.put("/api/ai-provider", json={
        "provider": "bedrock", "model": "anthropic.claude-sonnet-5-v1:0",
        "aws_access_key": "AKIA123", "aws_secret_key": "shhh",
        "aws_region": "eu-central-1",
    }, headers=auth).json()
    assert bedrock["has_aws_keys"] is True
    assert "shhh" not in str(bedrock) and "AKIA" not in str(bedrock)


def test_llm_factory_requires_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # anthropic without a key -> unusable
    assert llm_for_settings({"provider": "anthropic"}) is None
    # vertex without a project -> unusable
    assert llm_for_settings({"provider": "vertex"}) is None
    # anthropic with a key -> a client is built (no network call happens here)
    llm = llm_for_settings({"provider": "anthropic", "anthropic_key": "sk-x",
                            "model": "claude-sonnet-5"})
    assert llm is not None
    # unknown provider -> unusable rather than crashing
    assert llm_for_settings({"provider": "watsonx"}) is None
