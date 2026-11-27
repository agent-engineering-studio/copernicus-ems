"""A short, checked description of an activation, written by a language model and saved.

Needs the ``geo`` extra for the numbers, and the ``ai`` extra
(``pip install 'copernicus-ems[ai]'``) for Claude; a local OpenAI-compatible
server (llama.cpp, a LiteLLM gateway) needs nothing more than ``httpx``.

:func:`describe` reads the official text and the library's numbers from disk
(:mod:`copernicus_ems.describe_input`), asks a :class:`Provider` once per
activation, checks the answer (:mod:`copernicus_ems.describe_checks`), asks once
more with the problems found when the description is rejected, and writes
``<directory>/<CODE>/description.json``. The file is rewritten only when its
inputs change or with ``force``. Only public EMS text and the library's numbers
are sent to the model.

The provider: :func:`provider_from_env`, or any object with ``name``, ``model``
and ``ask(system, user, schema) -> dict`` (:class:`Provider`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import httpx

from copernicus_ems.client import default_directory
from copernicus_ems.describe_checks import ANSWER_SCHEMA, FORBIDDEN, Checked, check
from copernicus_ems.describe_input import Source, facts, read_source
from copernicus_ems.errors import EmsError

if TYPE_CHECKING:
    import anthropic

PROMPT_VERSION = 1
"""Raised whenever the prompt changes: every description is then written again."""

DESCRIPTION_FILE = "description.json"
CLAUDE_MODEL = "claude-opus-5-5"
BETA = "server-side-fallback-2026-07-01"
"""The beta of Claude's server-side refusal fallback, needed by ``fallbacks="default"``."""

ENV_LLM = "COPERNICUS_EMS_LLM"
ENV_LLM_URL = "COPERNICUS_EMS_LLM_URL"
ENV_LLM_MODEL = "COPERNICUS_EMS_LLM_MODEL"
ENV_CLAUDE_MODEL = "COPERNICUS_EMS_CLAUDE_MODEL"

NO_TEXT = "no official text"
HINT = (
    f"set ANTHROPIC_API_KEY for Claude, or start a local model with "
    f"`docker compose --profile ai up -d llm` and set {ENV_LLM_URL} "
    f"(e.g. http://127.0.0.1:8080/v1); {ENV_LLM}=claude|local chooses explicitly"
)

SYSTEM = """\
You write a short description of one activation of the Copernicus Emergency \
Management Service (a flood), in Italian ("it") and in English ("en"), for \
people who are not experts, and you extract a few fields from its official text.

You are given, as JSON:
- "source": the official text ("reason"), the titles and links of the \
information bulletins, the codes of related activations;
- "facts": the library's numbers, per area of interest (AOI): "water_km2" (the \
most water seen on one map), "observed_km2" (the area observed), "first_map" and \
"last_map" (dates of the maps), "sensors", "maps" (how many maps), and "radar" \
(how many maps a Sentinel-1 radar check confirms, marks to look at, or cannot \
assess).

Rules:
- Use only the facts and the official text. Never add a number, a date, a \
place, a name or a casualty that is not in them.
- "description": 3 to 5 sentences in each language: what happened, where, \
when, how much water the maps show, how far the radar backs them.
- Write every number in digits, never in words.
- Write place and river names exactly as the official text writes them; do \
not translate them.
- Never judge the maps: never call a map wrong, an error or a mistake. Never \
call water seen only by the radar missed water: radar candidates are areas to \
look at, not flooding the maps missed.
- "places", "rivers", "related_events", "casualties": each with "quote", the \
exact words copied from the official text, character for character, that \
contain the value. Leave a list empty when the text names nothing.
- "related_events": "date" as YYYY-MM-DD when the text gives the day, else "".
- "casualties": null when the official text gives no number of deaths; never \
guess one.
"""


@runtime_checkable
class Provider(Protocol):
    """A language model that answers with a JSON object following ``schema``."""

    name: str
    """``"claude"`` or ``"local"``: written in the file."""
    model: str
    """The model asked for (part of the inputs hash). A provider may also set
    ``last_model``, the model that actually answered the last ``ask`` (another
    one after a server-side fallback): the file records that one."""

    def ask(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """The model's answer, parsed; :class:`~copernicus_ems.EmsError` when there is none."""
        ...


class ClaudeProvider:
    """Claude through the official ``anthropic`` SDK (the ``ai`` extra).

    Structured output (``output_config.format`` with the JSON schema), the
    server-side refusal fallback (``fallbacks="default"``, beta
    ``server-side-fallback-2026-07-01``) and ``stop_reason`` checked. The model
    is ``model``, else ``COPERNICUS_EMS_CLAUDE_MODEL``, else ``claude-opus-5-5``.
    Without ``client``, ``anthropic.Anthropic()`` reads the credentials from the
    environment.
    """

    name = "claude"
    max_tokens = 16000

    def __init__(self, client: anthropic.Anthropic | None = None, model: str | None = None):
        self.model = model or os.environ.get(ENV_CLAUDE_MODEL, "").strip() or CLAUDE_MODEL
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - exercised only without the extra
                raise EmsError(
                    "Claude needs the ai extra: pip install 'copernicus-ems[ai]'"
                ) from exc
            client = anthropic.Anthropic()
        self._client = client
        self.last_model: str | None = None
        """The model that answered the last request: ``response.model``."""

    def ask(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """One request; refusals, truncation and SDK errors become ``EmsError``."""
        import anthropic

        who = f"Claude ({self.model})"
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                betas=[BETA],
                fallbacks="default",
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": schema},
                },
            )
        except anthropic.AuthenticationError as exc:
            raise EmsError(f"{who}: the credentials were refused ({exc.message})") from exc
        except anthropic.PermissionDeniedError as exc:
            raise EmsError(f"{who}: the key may not use this model ({exc.message})") from exc
        except anthropic.NotFoundError as exc:
            raise EmsError(f"{who}: unknown model or endpoint ({exc.message})") from exc
        except anthropic.RateLimitError as exc:
            raise EmsError(f"{who}: rate limited, retry later ({exc.message})") from exc
        except anthropic.BadRequestError as exc:
            raise EmsError(f"{who}: the request was refused ({exc.message})") from exc
        except anthropic.APIStatusError as exc:
            raise EmsError(f"{who}: the API answered {exc.status_code} ({exc.message})") from exc
        except anthropic.APITimeoutError as exc:
            raise EmsError(f"{who}: the request timed out") from exc
        except anthropic.APIConnectionError as exc:
            raise EmsError(f"{who}: could not reach the API ({exc})") from exc
        except anthropic.AnthropicError as exc:
            raise EmsError(f"{who}: {exc}") from exc
        except TypeError as exc:
            # The SDK raises a TypeError, not an AnthropicError, when it finds no
            # API key, auth token or profile to sign the request with.
            if "authentication" not in str(exc):
                raise
            raise EmsError(f"{who}: no credentials; {HINT}") from exc

        self.last_model = getattr(response, "model", None) or self.model

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            why = f" (category: {category})" if category else ""
            raise EmsError(f"{who} declined to write the description{why}")
        if response.stop_reason == "max_tokens":
            raise EmsError(f"{who}: the answer was cut at {self.max_tokens} tokens")
        # After a server-side fallback, the answer is the text after the last
        # ``fallback`` block (what came before was the declining model's).
        blocks = list(response.content)
        switches = [i for i, b in enumerate(blocks) if b.type == "fallback"]
        texts = [b.text for b in blocks[switches[-1] + 1 if switches else 0 :] if b.type == "text"]
        if not texts:
            raise EmsError(f"{who}: the answer has no text")
        return _object(texts[-1], who)


class LocalProvider:
    """Any OpenAI-compatible ``/chat/completions`` server: llama.cpp, a LiteLLM gateway.

    The answer is constrained by ``response_format`` (``json_schema``), which
    llama.cpp enforces. The timeout is long: a 3B model on a CPU takes tens of
    seconds.
    """

    name = "local"

    def __init__(
        self,
        url: str,
        model: str,
        client: httpx.Client | None = None,
        timeout: float = 300,
    ) -> None:
        self.url = url
        self.model = model
        self.timeout = timeout
        self._client = client

    def ask(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """One request; an unreachable server or a malformed answer becomes ``EmsError``."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "description", "schema": schema, "strict": True},
            },
        }
        endpoint = f"{self.url.rstrip('/')}/chat/completions"
        who = f"the local model at {self.url}"
        try:
            if self._client is not None:
                response = self._client.post(endpoint, json=body, timeout=self.timeout)
            else:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(endpoint, json=body)
        except httpx.HTTPError as exc:
            raise EmsError(
                f"{who} did not answer ({exc!r}); start it with "
                "`docker compose --profile ai up -d llm`"
            ) from exc
        if response.is_error:
            raise EmsError(f"{who} answered {response.status_code}: {response.text[:200]}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise EmsError(f"{who}: malformed answer") from exc
        return _object(content, who)


def _object(text: Any, who: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise EmsError(f"{who}: malformed answer, not JSON") from exc
    if not isinstance(value, dict):
        raise EmsError(f"{who}: malformed answer, not a JSON object")
    return value


def _credentials() -> bool:
    return any(os.environ.get(n, "").strip() for n in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"))


def provider_from_env() -> Provider:
    """The provider the environment asks for.

    ``COPERNICUS_EMS_LLM`` = ``claude`` | ``local`` chooses. Unset: Claude when
    ``ANTHROPIC_API_KEY`` or ``ANTHROPIC_AUTH_TOKEN`` is set (an ``ant auth
    login`` profile is not detected: set ``COPERNICUS_EMS_LLM=claude`` to use
    it), else the local server at ``COPERNICUS_EMS_LLM_URL`` with
    ``COPERNICUS_EMS_LLM_MODEL`` (``"default"`` when unset; llama.cpp ignores
    it), else :class:`~copernicus_ems.EmsError` saying how to get one.
    """
    choice = os.environ.get(ENV_LLM, "").strip().lower()
    url = os.environ.get(ENV_LLM_URL, "").strip()
    if choice not in ("", "claude", "local"):
        raise EmsError(f"{ENV_LLM}={choice!r}: expected claude or local")
    if choice == "claude" or (not choice and _credentials()):
        return ClaudeProvider()
    if url:
        return LocalProvider(url, os.environ.get(ENV_LLM_MODEL, "").strip() or "default")
    if choice == "local":
        raise EmsError(f"{ENV_LLM}=local needs {ENV_LLM_URL}: {HINT}")
    raise EmsError(f"no language model configured: {HINT}")


@dataclass(frozen=True)
class Description:
    """``description.json``: a model-written description and the fields read from the text.

    ``description`` is ``{"it", "en"}``, or ``None`` with ``reason`` saying why.
    Each field carries the ``quote`` of the official text it comes from;
    ``dropped`` lists what the checks removed and why.
    """

    activation: str
    generated_at: str
    provider: str
    model: str
    prompt_version: int
    inputs_hash: str
    source: dict[str, Any]
    facts: dict[str, Any]
    description: dict[str, str] | None
    reason: str | None
    places: list[dict[str, str]] = field(default_factory=list)
    rivers: list[dict[str, str]] = field(default_factory=list)
    related_events: list[dict[str, str]] = field(default_factory=list)
    casualties: dict[str, Any] | None = None
    dropped: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready, keys in the file's order."""
        return asdict(self)


_KEYS = list(Description.__dataclass_fields__)


def description_path(code: str, directory: Path | None = None) -> Path:
    """``<data directory>/EMSR762/description.json``."""
    return (directory or default_directory()) / code / DESCRIPTION_FILE


def read_description(code: str, directory: Path | None = None) -> Description | None:
    """The saved description; ``None`` when there is none or the file is not one."""
    try:
        body = json.loads(description_path(code, directory).read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict) or sorted(body) != sorted(_KEYS):
        return None
    try:
        return Description(**body)
    except TypeError:  # pragma: no cover - the keys were checked above
        return None


def inputs_hash(source: Source, facts: dict[str, Any], provider: Provider) -> str:
    """SHA-256 of the official text, the numbers, :data:`PROMPT_VERSION`, provider and model.

    The provider's name and model (the model asked for, not the one that
    answered after a fallback) are part of it: another model writes the
    description again, a fallback does not change the hash.
    """
    inputs = {
        "source": source.as_dict(),
        "facts": facts,
        "prompt_version": PROMPT_VERSION,
        "provider": provider.name,
        "model": provider.model,
    }
    canonical = json.dumps(inputs, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _user(source: Source, facts: dict[str, Any]) -> str:
    inputs = {"source": source.as_dict(), "facts": facts}
    return "Describe this activation.\n\n" + json.dumps(inputs, ensure_ascii=False, indent=2)


def _retry(user: str, answer: dict[str, Any], problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        f"{user}\n\nYour previous answer:\n{json.dumps(answer, ensure_ascii=False, indent=2)}"
        f"\n\nIts description was rejected for these problems:\n{listed}\n\n"
        "Write the whole answer again, fixing every problem and following every rule."
    )


_JUDGING = [re.compile(rf"\b(?:{p})\b", re.IGNORECASE) for ps in FORBIDDEN.values() for p in ps]


def _said(problem: str) -> str:
    """A problem as the file says it: a forbidden word is never repeated there."""
    if any(p.search(problem) for p in _JUDGING):
        language = problem.split(":", 1)[0]
        return f"{language}: uses a word that judges the maps"
    return problem


def _write(path: Path, description: Description) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(description.as_dict(), ensure_ascii=False, indent=2, allow_nan=False)
    part = path.with_name(path.name + ".part")
    part.write_text(text + "\n", encoding="utf-8")
    part.replace(path)


def describe(
    code: str,
    directory: Path | None = None,
    *,
    provider: Provider | None = None,
    force: bool = False,
) -> Description:
    """Describe an activation saved by :func:`~copernicus_ems.init`, and save the result.

    Returns the saved description, without asking the model, when its inputs
    hash is unchanged (also when the description is ``None``) and not
    ``force``. With no official text, no model is asked: ``description`` is
    ``None`` and ``reason`` is ``"no official text"``. A description rejected
    twice is ``None`` with the problems in ``reason``; the checked fields are
    kept. ``provider`` defaults to :func:`provider_from_env`.
    """
    root = directory or default_directory()
    source = read_source(code, root)
    numbers = facts(code, root)
    model = provider or provider_from_env()
    digest = inputs_hash(source, numbers, model)
    path = description_path(code, root)
    if not force:
        saved = read_description(code, root)
        if saved is not None and saved.inputs_hash == digest:
            return saved

    reason: str | None = NO_TEXT
    served = model.model
    if not source.reason.strip():
        checked = Checked(description=None)
    else:
        user = _user(source, numbers)
        answer = model.ask(SYSTEM, user, ANSWER_SCHEMA)
        checked = check(answer, source, numbers)
        if checked.problems:
            answer = model.ask(SYSTEM, _retry(user, answer, checked.problems), ANSWER_SCHEMA)
            checked = check(answer, source, numbers)
        served = getattr(model, "last_model", None) or model.model
        reason = (
            "the description was rejected twice: " + "; ".join(map(_said, checked.problems))
            if checked.problems
            else None
        )

    result = Description(
        activation=code,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        provider=model.name,
        model=served,
        prompt_version=PROMPT_VERSION,
        inputs_hash=digest,
        source=source.as_dict(),
        facts=numbers,
        description=checked.description,
        reason=reason,
        places=checked.places,
        rivers=checked.rivers,
        related_events=checked.related_events,
        casualties=checked.casualties,
        dropped=checked.dropped,
    )
    _write(path, result)
    return result
