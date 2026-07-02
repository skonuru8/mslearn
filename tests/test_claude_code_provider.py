import json
import subprocess

import pytest

from mslearn.providers.base import (
    ModelMessage,
    ModelRequest,
    ProviderBadOutputError,
    ProviderTransientError,
)
from mslearn.providers.claude_code import ClaudeCodeProvider


def fake_run(result_text, returncode=0, usage=None):
    def _run(cmd, **kwargs):
        fake_run.last_cmd, fake_run.last_input = cmd, kwargs.get("input")
        out = json.dumps({"result": result_text, "usage": usage or {}})
        return subprocess.CompletedProcess(cmd, returncode, stdout=out, stderr="err")
    return _run


def req(schema=None, system=None):
    msgs = ([ModelMessage(role="system", content=system)] if system else [])
    msgs.append(ModelMessage(role="user", content="hi"))
    return ModelRequest(messages=msgs, json_schema=schema)


def test_complete_invokes_headless_json_mode(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        fake_run("hello", usage={"input_tokens": 4, "output_tokens": 6}))
    resp = ClaudeCodeProvider().complete("default", req(system="be brief"))
    cmd = fake_run.last_cmd
    assert cmd[:2] == ["claude", "-p"]
    assert "--output-format" in cmd and "json" in cmd
    assert "--append-system-prompt" in cmd and "be brief" in cmd
    assert fake_run.last_input == "hi"
    assert resp.text == "hello" and resp.input_tokens == 4 and resp.output_tokens == 6
    assert resp.provider == "claude_code"


def test_schema_instruction_appended_and_parsed(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run('{"a": 3}'))
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    resp = ClaudeCodeProvider().complete("default", req(schema=schema))
    assert "JSON" in fake_run.last_input and '"integer"' in fake_run.last_input
    assert resp.parsed == {"a": 3}


def test_bad_json_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run("not json"))
    with pytest.raises(ProviderBadOutputError):
        ClaudeCodeProvider().complete("default", req(schema={"type": "object"}))


def test_nonzero_exit_is_transient(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run("", returncode=1))
    with pytest.raises(ProviderTransientError):
        ClaudeCodeProvider().complete("default", req())


def test_explicit_model_flag(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run("ok"))
    ClaudeCodeProvider().complete("opus", req())
    assert "--model" in fake_run.last_cmd and "opus" in fake_run.last_cmd


def test_non_json_stdout_raises_bad_output(monkeypatch):
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="warning: not json", stderr="")
    monkeypatch.setattr(subprocess, "run", _run)
    with pytest.raises(ProviderBadOutputError):
        ClaudeCodeProvider().complete("default", req())


def test_null_result_treated_as_empty(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(None))
    resp = ClaudeCodeProvider().complete("default", req())
    assert resp.text == ""


def test_null_result_with_schema_raises_bad_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(None))
    with pytest.raises(ProviderBadOutputError):
        ClaudeCodeProvider().complete("default", req(schema={"type": "object"}))
