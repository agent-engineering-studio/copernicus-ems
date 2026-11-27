"""Providers, describe() and the description file (src/copernicus_ems/describe.py).

No real model and no network: the local provider through ``httpx.MockTransport``,
Claude through an injected fake client.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
from test_describe_input import add_text, saved  # noqa: F401 - the fixture

import copernicus_ems
from copernicus_ems import EmsError
from copernicus_ems.cli import main
from copernicus_ems.describe import (
    BETA,
    PROMPT_VERSION,
    ClaudeProvider,
    Description,
    LocalProvider,
    Provider,
    describe,
    description_path,
    provider_from_env,
    read_description,
)
from copernicus_ems.describe_checks import ANSWER_SCHEMA

KEYS = [
    "activation",
    "generated_at",
    "provider",
    "model",
    "prompt_version",
    "inputs_hash",
    "source",
    "facts",
    "description",
    "reason",
    "places",
    "rivers",
    "related_events",
    "casualties",
    "dropped",
]

VALID: dict[str, Any] = {
    "description": {
        "it": "Una nuova ondata di maltempo ha colpito l'Emilia-Romagna il 17 maggio 2023.",
        "en": "A new wave of severe weather hit Emilia-Romagna on 17 May 2023.",
    },
    "places": [{"name": "Emilia-Romagna", "quote": "hit Emilia-Romagna on 17 May 2023"}],
    "rivers": [],
    "related_events": [],
    "casualties": {"deaths": 3, "quote": "with 3 deaths"},
}


def invented() -> dict[str, Any]:
    answer = copy.deepcopy(VALID)
    answer["description"]["en"] += " The maps show 42 km² of water."
    answer["places"].append({"name": "Narnia", "quote": "a land called Narnia"})
    return answer


class Fake:
    """A provider that answers from a list and remembers what it was asked."""

    def __init__(self, *answers: dict[str, Any], name: str = "claude") -> None:
        self.name = name
        self.model = "fake-model"
        self.answers = list(answers)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def ask(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user, schema))
        return self.answers.pop(0)


@pytest.fixture
def text(saved: Path) -> Path:  # noqa: F811 - the imported fixture
    add_text(saved)
    return saved


def test_fake_is_a_provider() -> None:
    assert isinstance(Fake(), Provider)


def test_valid_flow_writes_every_key(text: Path) -> None:
    fake = Fake(VALID)
    result = describe("EMSR762", provider=fake)
    assert len(fake.calls) == 1
    system, user, schema = fake.calls[0]
    assert schema == ANSWER_SCHEMA
    assert "digits" in system and "quote" in system
    sent = json.loads(user[user.index("{") :])
    assert sent["source"]["reason"].startswith("A new wave")
    assert sent["facts"]["aois"]
    path = description_path("EMSR762")
    assert path == text / "EMSR762" / "description.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert list(body) == KEYS
    assert body["activation"] == "EMSR762"
    assert body["provider"] == "claude" and body["model"] == "fake-model"
    assert body["prompt_version"] == PROMPT_VERSION
    assert body["description"] == VALID["description"]
    assert body["reason"] is None
    assert body["places"] == VALID["places"]
    assert body["casualties"] == VALID["casualties"]
    assert body["dropped"] == []
    assert body["source"]["code"] == "EMSR762"
    assert body["facts"] == sent["facts"]
    assert result.as_dict() == body
    assert read_description("EMSR762") == result
    assert not list((text / "EMSR762").glob("*.part"))


def test_retry_carries_the_problems(text: Path) -> None:
    fake = Fake(invented(), VALID)
    result = describe("EMSR762", provider=fake)
    assert len(fake.calls) == 2
    retry = fake.calls[1][1]
    assert '"42"' in retry  # the problem names the number
    assert "42 km²" in retry  # the previous answer is there too
    assert result.description == VALID["description"]
    assert result.dropped == []


def test_two_rejections_keep_the_fields_and_are_cached(text: Path) -> None:
    fake = Fake(invented(), invented())
    result = describe("EMSR762", provider=fake)
    assert len(fake.calls) == 2
    assert result.description is None
    assert result.reason and "42" in result.reason
    assert result.places == VALID["places"]
    assert result.casualties == VALID["casualties"]
    assert [d["field"] for d in result.dropped] == ["places"]
    body = json.loads(description_path("EMSR762").read_text(encoding="utf-8"))
    assert body["description"] is None and body["reason"] == result.reason

    again = Fake()
    assert describe("EMSR762", provider=again) == result
    assert again.calls == []


def test_cache_hit_makes_no_call_and_force_does(text: Path) -> None:
    first = describe("EMSR762", provider=Fake(VALID))
    cached = Fake()
    assert describe("EMSR762", provider=cached) == first
    assert cached.calls == []

    forced = Fake(VALID)
    describe("EMSR762", provider=forced, force=True)
    assert len(forced.calls) == 1


def test_inputs_change_regenerates(text: Path) -> None:
    first = describe("EMSR762", provider=Fake(VALID))
    other = Fake(VALID)
    other.model = "another-model"
    second = describe("EMSR762", provider=other)
    assert len(other.calls) == 1
    assert second.inputs_hash != first.inputs_hash
    assert second.model == "another-model"


def test_no_reason_no_call(saved: Path) -> None:  # noqa: F811
    path = saved / "EMSR762" / "detail.json"
    body = json.loads(path.read_bytes())
    body["results"][0]["reason"] = ""
    path.write_text(json.dumps(body))
    fake = Fake()
    result = describe("EMSR762", provider=fake)
    assert fake.calls == []
    assert result.description is None
    assert result.reason == "no official text"
    assert result.places == result.rivers == result.related_events == result.dropped == []
    assert result.casualties is None
    assert description_path("EMSR762").is_file()


def test_read_description_missing_or_malformed(saved: Path) -> None:  # noqa: F811
    assert read_description("EMSR762") is None
    description_path("EMSR762").write_text("{not json", encoding="utf-8")
    assert read_description("EMSR762") is None
    description_path("EMSR762").write_text('{"activation": "EMSR762"}', encoding="utf-8")
    assert read_description("EMSR762") is None


def test_malformed_cache_is_regenerated(text: Path) -> None:
    description_path("EMSR762").write_text("[]", encoding="utf-8")
    fake = Fake(VALID)
    describe("EMSR762", provider=fake)
    assert len(fake.calls) == 1


def test_lazy_export() -> None:
    assert copernicus_ems.Description is Description
    assert copernicus_ems.provider_from_env is provider_from_env
    with pytest.raises(AttributeError):
        _ = copernicus_ems.not_a_name


# --- the local provider -----------------------------------------------------------


def local(handler: Any, url: str = "http://llm:8080/v1") -> LocalProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LocalProvider(url, "qwen", client=client)


def test_local_request_and_answer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        content = json.dumps(VALID)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = local(handler)
    assert provider.name == "local" and provider.model == "qwen"
    assert provider.ask("SYS", "USER", ANSWER_SCHEMA) == VALID
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == "http://llm:8080/v1/chat/completions"
    body = json.loads(request.content)
    assert body["model"] == "qwen"
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "description", "schema": ANSWER_SCHEMA, "strict": True},
    }


def test_local_unreachable_names_url_and_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(EmsError, match=r"http://llm:8080/v1.*--profile ai up -d llm"):
        local(handler).ask("S", "U", ANSWER_SCHEMA)


def test_local_http_error() -> None:
    with pytest.raises(EmsError, match="503"):
        local(lambda r: httpx.Response(503, text="busy")).ask("S", "U", ANSWER_SCHEMA)


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"message": {"content": "not json"}}]},
        {"choices": [{"message": {"content": "[1, 2]"}}]},
    ],
)
def test_local_malformed_answer(body: dict[str, Any]) -> None:
    with pytest.raises(EmsError, match="malformed"):
        local(lambda r: httpx.Response(200, json=body)).ask("S", "U", ANSWER_SCHEMA)


def test_local_own_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(VALID)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    real = httpx.Client

    def fake_client(**kwargs: Any) -> httpx.Client:
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)
    assert LocalProvider("http://x/v1/", "m").ask("S", "U", ANSWER_SCHEMA) == VALID


# --- Claude ------------------------------------------------------------------------


class FakeMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


def claude(response: Any = None, error: Exception | None = None) -> tuple[ClaudeProvider, Any]:
    messages = FakeMessages(response, error)
    client: Any = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return ClaudeProvider(client=client), messages


def reply(
    *blocks: Any, stop: str = "end_turn", details: Any = None, model: str | None = None
) -> Any:
    return SimpleNamespace(
        content=list(blocks), stop_reason=stop, stop_details=details, model=model
    )


def text_block(value: str) -> Any:
    return SimpleNamespace(type="text", text=value)


def test_claude_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COPERNICUS_EMS_CLAUDE_MODEL", raising=False)
    provider, messages = claude(reply(text_block(json.dumps(VALID))))
    assert provider.name == "claude" and provider.model == "claude-opus-5-5"
    assert provider.ask("SYS", "USER", ANSWER_SCHEMA) == VALID
    sent = messages.kwargs
    assert sent["model"] == "claude-opus-5-5"
    assert sent["betas"] == [BETA] == ["server-side-fallback-2026-07-01"]
    assert sent["fallbacks"] == "default"
    assert sent["system"] == "SYS"
    assert sent["messages"] == [{"role": "user", "content": "USER"}]
    assert sent["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
    }
    assert sent["max_tokens"] >= 4096
    assert "temperature" not in sent and "top_p" not in sent


def test_claude_model_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPERNICUS_EMS_CLAUDE_MODEL", "claude-sonnet-5-5")
    provider, _ = claude()
    assert provider.model == "claude-sonnet-5-5"


def test_claude_text_after_fallback() -> None:
    fallback = SimpleNamespace(type="fallback")
    response = reply(
        SimpleNamespace(type="thinking", thinking=""),
        text_block('{"partial'),
        fallback,
        text_block(json.dumps(VALID)),
    )
    provider, _ = claude(response)
    assert provider.ask("S", "U", ANSWER_SCHEMA) == VALID


def test_claude_refusal() -> None:
    provider, _ = claude(reply(stop="refusal"))
    with pytest.raises(EmsError, match="declined"):
        provider.ask("S", "U", ANSWER_SCHEMA)


@pytest.mark.parametrize(
    "response",
    [reply(stop="max_tokens"), reply(), reply(text_block("not json")), reply(text_block("[]"))],
)
def test_claude_unusable_answer(response: Any) -> None:
    provider, _ = claude(response)
    with pytest.raises(EmsError, match="Claude"):
        provider.ask("S", "U", ANSWER_SCHEMA)


REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(
    ("error", "says"),
    [
        (
            anthropic.AuthenticationError(
                "bad key", response=httpx2.Response(401, request=REQUEST), body=None
            ),
            "credentials",
        ),
        (
            anthropic.RateLimitError(
                "slow down", response=httpx2.Response(429, request=REQUEST), body=None
            ),
            "rate",
        ),
        (
            anthropic.InternalServerError(
                "boom", response=httpx2.Response(500, request=REQUEST), body=None
            ),
            "500",
        ),
        (
            anthropic.PermissionDeniedError(
                "no", response=httpx2.Response(403, request=REQUEST), body=None
            ),
            "may not use",
        ),
        (
            anthropic.NotFoundError(
                "gone", response=httpx2.Response(404, request=REQUEST), body=None
            ),
            "unknown model",
        ),
        (
            anthropic.BadRequestError(
                "bad", response=httpx2.Response(400, request=REQUEST), body=None
            ),
            "request was refused",
        ),
        (anthropic.CredentialsError("no profile"), "no profile"),
        (anthropic.APITimeoutError(request=REQUEST), "timed out"),
        (anthropic.APIConnectionError(request=REQUEST), "reach"),
    ],
)
def test_claude_errors_become_ems_errors(error: Exception, says: str) -> None:
    provider, _ = claude(error=error)
    with pytest.raises(EmsError, match=says):
        provider.ask("S", "U", ANSWER_SCHEMA)


# --- provider_from_env --------------------------------------------------------------

ENV = (
    "COPERNICUS_EMS_LLM",
    "COPERNICUS_EMS_LLM_URL",
    "COPERNICUS_EMS_LLM_MODEL",
    "COPERNICUS_EMS_CLAUDE_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"ANTHROPIC_API_KEY": "k"}, "claude"),
        ({"ANTHROPIC_AUTH_TOKEN": "t"}, "claude"),
        ({"ANTHROPIC_API_KEY": "k", "COPERNICUS_EMS_LLM_URL": "http://llm/v1"}, "claude"),
        ({"COPERNICUS_EMS_LLM_URL": "http://llm/v1"}, "local"),
        (
            {"COPERNICUS_EMS_LLM": "local", "COPERNICUS_EMS_LLM_URL": "http://llm/v1"},
            "local",
        ),
        (
            {
                "COPERNICUS_EMS_LLM": "local",
                "ANTHROPIC_API_KEY": "k",
                "COPERNICUS_EMS_LLM_URL": "http://llm/v1",
            },
            "local",
        ),
        ({"COPERNICUS_EMS_LLM": "Claude"}, "claude"),
    ],
)
def test_provider_from_env(env: pytest.MonkeyPatch, values: dict[str, str], expected: str) -> None:
    for name, value in values.items():
        env.setenv(name, value)
    assert provider_from_env().name == expected


def test_local_from_env_model(env: pytest.MonkeyPatch) -> None:
    env.setenv("COPERNICUS_EMS_LLM_URL", "http://llm/v1")
    env.setenv("COPERNICUS_EMS_LLM_MODEL", "qwen2.5-3b")
    provider = provider_from_env()
    assert isinstance(provider, LocalProvider)
    assert provider.model == "qwen2.5-3b" and provider.url == "http://llm/v1"


def test_claude_from_env_model(env: pytest.MonkeyPatch) -> None:
    env.setenv("ANTHROPIC_API_KEY", "k")
    env.setenv("COPERNICUS_EMS_CLAUDE_MODEL", "claude-haiku-5-5")
    assert provider_from_env().model == "claude-haiku-5-5"


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"ANTHROPIC_API_KEY": " "},
        {"COPERNICUS_EMS_LLM": "local"},
        {"COPERNICUS_EMS_LLM": "gpt"},
    ],
)
def test_provider_from_env_fails_with_hint(env: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for name, value in values.items():
        env.setenv(name, value)
    with pytest.raises(EmsError, match="COPERNICUS_EMS_LLM"):
        provider_from_env()


def test_describe_uses_env_provider(text: Path, env: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(VALID)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    real = httpx.Client
    env.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    env.setenv("COPERNICUS_EMS_LLM_URL", "http://llm/v1")
    result = describe("EMSR762")
    assert result.provider == "local"
    assert isinstance(result, Description)


def test_claude_without_credentials() -> None:
    error = TypeError(
        "Could not resolve authentication method. Expected one of api_key, auth_token, "
        "or credentials to be set."
    )
    provider, _ = claude(error=error)
    with pytest.raises(EmsError, match=r"no credentials.*ANTHROPIC_API_KEY"):
        provider.ask("S", "U", ANSWER_SCHEMA)


def test_claude_other_type_error_is_not_hidden() -> None:
    provider, _ = claude(error=TypeError("unexpected keyword"))
    with pytest.raises(TypeError, match="unexpected keyword"):
        provider.ask("S", "U", ANSWER_SCHEMA)


def test_claude_refusal_category() -> None:
    provider, _ = claude(reply(stop="refusal", details=SimpleNamespace(category="cyber")))
    with pytest.raises(EmsError, match=r"declined.*category: cyber"):
        provider.ask("S", "U", ANSWER_SCHEMA)


def test_served_model_is_written_and_cache_stays(
    text: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COPERNICUS_EMS_CLAUDE_MODEL", raising=False)
    response = reply(text_block(json.dumps(VALID)), model="claude-opus-4-8")
    provider, messages = claude(response)
    first = describe("EMSR762", provider=provider)
    assert messages.kwargs["model"] == "claude-opus-5-5"
    assert provider.last_model == "claude-opus-4-8"
    assert first.model == "claude-opus-4-8"
    body = json.loads(description_path("EMSR762").read_text(encoding="utf-8"))
    assert body["model"] == "claude-opus-4-8"

    again, messages = claude()
    assert describe("EMSR762", provider=again) == first  # hashed on the model asked for
    assert messages.kwargs == {}


def test_no_text_records_the_model_asked_for(saved: Path) -> None:  # noqa: F811
    path = saved / "EMSR762" / "detail.json"
    body = json.loads(path.read_bytes())
    body["results"][0]["reason"] = ""
    path.write_text(json.dumps(body))
    fake = Fake()
    fake.last_model = "stale-from-another-activation"  # type: ignore[attr-defined]
    assert describe("EMSR762", provider=fake).model == "fake-model"


def test_reason_never_repeats_a_judging_word(text: Path) -> None:
    judging = copy.deepcopy(VALID)
    judging["description"]["en"] += " The map is wrong."
    fake = Fake(judging, judging)
    result = describe("EMSR762", provider=fake)
    assert '"wrong"' in fake.calls[1][1]  # the model is told which word
    assert result.description is None and result.reason
    assert "wrong" not in result.reason.lower()
    assert "en: uses a word that judges the maps" in result.reason
    raw = description_path("EMSR762").read_text(encoding="utf-8").lower()
    assert '"wrong' not in raw and 'wrong"' not in raw


# --- the command line ---


def cli(monkeypatch: pytest.MonkeyPatch, fake: Fake) -> None:
    monkeypatch.setattr("copernicus_ems.cli.provider_from_env", lambda: fake)


def test_cli_one_activation(
    text: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = Fake(VALID)
    cli(monkeypatch, fake)
    assert main(["describe", "EMSR762"]) == 0
    assert capsys.readouterr().out == "EMSR762\tclaude/fake-model\tdescription: ok\t0 dropped\n"
    assert main(["describe", "EMSR762"]) == 0  # cached
    assert len(fake.calls) == 1
    fake.answers.append(VALID)
    assert main(["describe", "EMSR762", "--force"]) == 0
    assert len(fake.calls) == 2


def test_cli_json(
    text: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli(monkeypatch, Fake(VALID))
    assert main(["describe", "EMSR762", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["activation"] == "EMSR762"


def test_cli_all_continues_and_exits_1(
    text: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Failing(Fake):
        def ask(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            raise EmsError("model down")

    cli(monkeypatch, Failing())
    assert main(["describe", "--all"]) == 1
    captured = capsys.readouterr()
    assert "EMSR762\tfailed: model down" in captured.err
    assert "EMSR935\tfailed:" in captured.err  # the others are still tried
    assert "2 of 2 failed" in captured.err


def test_cli_all_keeps_going_after_a_failure(
    text: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli(monkeypatch, Fake(VALID))
    assert main(["describe", "--all"]) == 1  # EMSR935 has no detail saved
    captured = capsys.readouterr()
    assert "EMSR762\tclaude/fake-model\tdescription: ok" in captured.out
    assert "1 of 2 failed" in captured.err


def test_cli_none_line(
    saved: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = saved / "EMSR762" / "detail.json"
    body = json.loads(path.read_bytes())
    body["results"][0]["reason"] = ""
    path.write_text(json.dumps(body))
    cli(monkeypatch, Fake())
    assert main(["describe", "EMSR762"]) == 0
    assert "description: none (no official text)" in capsys.readouterr().out


def test_cli_no_provider_fails_once(
    saved: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in ("COPERNICUS_EMS_LLM", "COPERNICUS_EMS_LLM_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert main(["describe", "--all"]) == 1
    err = capsys.readouterr().err
    assert err.count("copernicus-ems:") == 1


def test_cli_needs_a_code_or_all(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["describe"]) == 2
    assert main(["describe", "EMSR762", "--all"]) == 2


def test_cli_all_without_init(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli(monkeypatch, Fake())
    assert main(["describe", "--all"]) == 1
    assert "init" in capsys.readouterr().err
