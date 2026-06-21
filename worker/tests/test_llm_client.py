import pytest

from app import clients
from app.llm import openai_client
from app.llm.openai_client import (
    DEFAULT_MODEL,
    LLMConfigurationError,
    OpenAIClient,
    OpenAIConfig,
    OpenAIError,
    load_config_from_env,
)


# --- Fake OpenAI SDK objects ---


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content, choices=None):
        self.choices = choices if choices is not None else [_FakeChoice(content)]


class RateLimitError(Exception):
    """Name matches the retryable set used by the client."""


class AuthenticationError(Exception):
    """Non-retryable error name."""


class _FakeCompletions:
    def __init__(self, behavior):
        self._behavior = behavior
        self.calls = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return self._behavior(self.calls)


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, behavior):
        self.chat = _FakeChat(_FakeCompletions(behavior))


def _client(behavior, **config_kwargs):
    config = OpenAIConfig(api_key="k", backoff_seconds=0, **config_kwargs)
    fake = _FakeClient(behavior)
    return OpenAIClient(config, client=fake), fake


# --- config loading ---


def test_load_config_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert load_config_from_env() is None


def test_load_config_defaults_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "abc")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    config = load_config_from_env()
    assert config is not None
    assert config.api_key == "abc"
    assert config.model == DEFAULT_MODEL


def test_load_config_custom_model_and_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "abc")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example.com/v1")
    config = load_config_from_env()
    assert config.model == "gpt-4o"
    assert config.base_url == "https://proxy.example.com/v1"


def test_from_env_raises_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMConfigurationError):
        OpenAIClient.from_env()


# --- complete: success + request shape ---


def test_complete_returns_content():
    client, fake = _client(lambda call: _FakeResponse('[]'))
    assert client.complete("prompt") == "[]"


def test_complete_sends_model_and_messages():
    client, fake = _client(lambda call: _FakeResponse("ok"), model="gpt-4o")
    client.complete("review this diff")

    kwargs = fake.chat.completions.last_kwargs
    assert kwargs["model"] == "gpt-4o"
    roles = [m["role"] for m in kwargs["messages"]]
    assert roles == ["system", "user"]
    assert kwargs["messages"][1]["content"] == "review this diff"


# --- complete: empty / malformed responses ---


def test_complete_empty_choices_returns_empty():
    client, _ = _client(lambda call: _FakeResponse("", choices=[]))
    assert client.complete("p") == ""


def test_complete_none_content_returns_empty():
    client, _ = _client(lambda call: _FakeResponse(None))
    assert client.complete("p") == ""


def test_complete_malformed_response_returns_empty():
    client, _ = _client(lambda call: object())  # no .choices attribute
    assert client.complete("p") == ""


# --- complete: retries + failures ---


def test_complete_retries_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(openai_client.time, "sleep", lambda s: sleeps.append(s))

    def behavior(call):
        if call == 1:
            raise RateLimitError("slow down")
        return _FakeResponse("recovered")

    client, fake = _client(behavior, max_retries=3)
    assert client.complete("p") == "recovered"
    assert fake.chat.completions.calls == 2
    assert len(sleeps) == 1  # backed off once before the retry


def test_complete_rate_limit_exhausts_retries(monkeypatch):
    monkeypatch.setattr(openai_client.time, "sleep", lambda s: None)

    def behavior(call):
        raise RateLimitError("always limited")

    client, fake = _client(behavior, max_retries=3)
    with pytest.raises(OpenAIError):
        client.complete("p")
    assert fake.chat.completions.calls == 3


def test_complete_non_retryable_raises_immediately(monkeypatch):
    monkeypatch.setattr(openai_client.time, "sleep", lambda s: None)

    def behavior(call):
        raise AuthenticationError("bad key")

    client, fake = _client(behavior, max_retries=3)
    with pytest.raises(OpenAIError):
        client.complete("p")
    assert fake.chat.completions.calls == 1  # no retry on non-transient error


# --- build_llm_client wiring ---


def test_build_llm_client_none_without_key(monkeypatch):
    monkeypatch.setattr(clients, "load_config_from_env", lambda: None)
    assert clients.build_llm_client() is None


def test_build_llm_client_returns_openai_client(monkeypatch):
    monkeypatch.setattr(
        clients, "load_config_from_env", lambda: OpenAIConfig(api_key="k")
    )
    client = clients.build_llm_client()
    assert isinstance(client, OpenAIClient)


# --- integration with the review engine ---


def test_engine_generates_comments_via_openai_client():
    from app import review_engine
    from app.context_retriever import DiffContextBundle, RetrievedContext

    payload = (
        '[{"file_path": "app/main.py", "line_number": 12, '
        '"severity": "high", "title": "SQL injection", '
        '"explanation": "user input is concatenated", '
        '"suggestion": "use parameters"}]'
    )

    client, fake = _client(lambda call: _FakeResponse(payload))

    bundle = DiffContextBundle(
        repository="octocat/hello",
        diff_file_path="app/main.py",
        chunk_index=0,
        diff_text="+ query = 'SELECT ' + user_input",
        retrieved_contexts=[
            RetrievedContext(
                repository="octocat/hello",
                file_path="app/db.py",
                language="python",
                start_line=1,
                end_line=5,
                content="def run(q): ...",
                score=0.9,
            )
        ],
    )

    result = review_engine.generate_review("octocat/hello", bundle, client)

    assert result.total_comments == 1
    comment = result.comments[0]
    assert comment.file_path == "app/main.py"
    assert comment.severity == "high"

    # Diff and context were injected into the user prompt.
    user_prompt = fake.chat.completions.last_kwargs["messages"][1]["content"]
    assert "SELECT ' + user_input" in user_prompt
    assert "app/db.py" in user_prompt


def test_engine_degrades_when_client_is_none():
    from app import review_engine
    from app.context_retriever import DiffContextBundle

    bundle = DiffContextBundle(
        repository="octocat/hello",
        diff_file_path="app/main.py",
        chunk_index=0,
        diff_text="+x = 1",
        retrieved_contexts=[],
    )
    result = review_engine.generate_review("octocat/hello", bundle, None)
    assert result.total_comments == 0
    assert result.comments == []
