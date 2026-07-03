import pytest
import respx

from mslearn.opsdb import OpsDB
from mslearn.profiles import load_profiles, set_active_profile_name
from mslearn.providers.base import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderError,
)
from mslearn.providers.router import ModelRouter
from mslearn.settings import Settings


class FakeProvider(ModelProvider):
    def __init__(self, name):
        self.name = name
        self.calls = []

    def complete(self, model, request):
        self.calls.append((model, request))
        return ModelResponse(text=f"{self.name}:{model}", parsed=None, input_tokens=1,
                             output_tokens=2, latency_ms=1.0, provider=self.name, model=model)

    def embed(self, model, texts):
        return [[0.0] * 3 for _ in texts]


class ExplodingProvider(FakeProvider):
    def complete(self, model, request):
        raise ProviderError("kaboom")


@pytest.fixture
def env(tmp_path):
    cfg = load_profiles("profiles.yaml")
    db = OpsDB(tmp_path / "ops.db")
    fakes = {"ollama": FakeProvider("ollama"), "openrouter": FakeProvider("openrouter"),
             "claude_code": FakeProvider("claude_code")}
    router = ModelRouter(cfg, db, Settings(_env_file=None), providers=fakes)
    return cfg, db, fakes, router


def request():
    return ModelRequest(messages=[ModelMessage(role="user", content="q")])


def test_routes_by_active_profile_role(env):
    cfg, db, fakes, router = env
    resp = router.complete("extraction", request())
    assert resp.provider == "openrouter" and resp.model == "deepseek/deepseek-v4-flash"
    resp = router.complete("synthesis", request())
    assert resp.provider == "openrouter" and resp.model == "deepseek/deepseek-v4-flash"


def test_profile_switch_changes_routing(env):
    cfg, db, fakes, router = env
    set_active_profile_name(db, cfg, "offline")
    resp = router.complete("synthesis", request())
    assert resp.provider == "ollama"


def test_success_and_failure_are_logged(env):
    cfg, db, fakes, router = env
    router.complete("interactive", request())
    fakes["openrouter"] = ExplodingProvider("openrouter")
    router._providers["openrouter"] = fakes["openrouter"]
    with pytest.raises(ProviderError):
        router.complete("interactive", request())
    calls = db.recent_calls()
    assert calls[0]["outcome"] == "error" and "kaboom" in calls[0]["error"]
    assert calls[1]["outcome"] == "ok" and calls[1]["role"] == "interactive"


def test_embed_uses_embedding_role(env):
    cfg, db, fakes, router = env
    vecs = router.embed(["x", "y"])
    assert len(vecs) == 2 and len(vecs[0]) == 3


def test_role_params_merged_into_request(env):
    cfg, db, fakes, router = env
    cfg.profiles["openrouter"].roles["synthesis"].params = {"temperature": 0.2}
    router.complete("synthesis", request())
    _, sent = fakes["openrouter"].calls[-1]
    assert sent.params["temperature"] == 0.2

    cfg.profiles["openrouter"].roles["synthesis"].params = {"temperature": 0.2, "top_p": 0.9}
    router.complete("synthesis", ModelRequest(
        messages=[ModelMessage(role="user", content="q")], params={"temperature": 0.7}))
    _, sent = fakes["openrouter"].calls[-1]
    assert sent.params["temperature"] == 0.7  # request wins on conflict
    assert sent.params["top_p"] == 0.9        # role param preserved


def test_embed_success_and_failure_are_logged(env):
    cfg, db, fakes, router = env
    router.embed(["x"])
    calls = db.recent_calls()
    assert calls[0]["role"] == "embedding" and calls[0]["outcome"] == "ok"

    class ExplodingEmbed(FakeProvider):
        def embed(self, model, texts):
            raise ProviderError("embed boom")

    router._providers["ollama"] = ExplodingEmbed("ollama")
    with pytest.raises(ProviderError):
        router.embed(["x"])
    assert db.recent_calls()[0]["outcome"] == "error"


def test_abandoned_stream_logs_abandoned(env):
    cfg, db, fakes, router = env

    class MultiChunk(FakeProvider):
        def stream(self, model, request):
            yield "a"
            yield "b"

    router._providers["openrouter"] = MultiChunk("openrouter")
    gen = router.stream("interactive", request())
    next(gen)
    gen.close()  # simulates consumer break
    calls = db.recent_calls()
    assert calls[0]["role"] == "interactive" and calls[0]["outcome"] == "abandoned"


def test_provider_error_logs_role_and_model(env, caplog):
    import logging

    cfg, db, fakes, router = env
    fakes["openrouter"] = ExplodingProvider("openrouter")
    router._providers["openrouter"] = fakes["openrouter"]
    with caplog.at_level(logging.INFO, logger="mslearn"), pytest.raises(ProviderError):
        router.complete("interactive", request())
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "role=interactive" in m and "deepseek/deepseek-chat" in m and "kaboom" in m
        for m in warnings
    )


def test_non_taxonomy_exception_still_logged(env):
    cfg, db, fakes, router = env

    class Rogue(FakeProvider):
        def complete(self, model, request):
            raise RuntimeError("rogue failure")

    router._providers["openrouter"] = Rogue("openrouter")
    with pytest.raises(RuntimeError):
        router.complete("interactive", request())
    assert db.recent_calls()[0]["outcome"] == "error"


def test_stream_non_taxonomy_exception_not_logged_as_ok(env):
    cfg, db, fakes, router = env

    class RogueStream(FakeProvider):
        def stream(self, model, request):
            yield "a"
            raise RuntimeError("mid-stream rogue")

    router._providers["openrouter"] = RogueStream("openrouter")
    with pytest.raises(RuntimeError):
        list(router.stream("interactive", request()))
    calls = db.recent_calls()
    assert calls[0]["outcome"] == "error" and "rogue" in calls[0]["error"]


def test_construction_never_raises_with_empty_openrouter_key(tmp_path):
    # OpenRouterProvider.__init__ raises when the key is empty. Providers
    # must be built lazily (on first use per name), not eagerly in
    # ModelRouter.__init__ — otherwise the app can never boot keyless, even
    # on the `offline` profile which never touches openrouter at all.
    cfg = load_profiles("profiles.yaml")
    db = OpsDB(tmp_path / "ops.db")
    settings = Settings(_env_file=None, openrouter_api_key="")
    router = ModelRouter(cfg, db, settings)  # must not raise
    assert router._providers == {}


@respx.mock
def test_offline_profile_works_end_to_end_with_empty_openrouter_key(tmp_path):
    cfg = load_profiles("profiles.yaml")
    db = OpsDB(tmp_path / "ops.db")
    set_active_profile_name(db, cfg, "offline")
    settings = Settings(_env_file=None, openrouter_api_key="", ollama_base_url="http://ollama.test")
    router = ModelRouter(cfg, db, settings)

    respx.post("http://ollama.test/api/chat").respond(
        json={"message": {"content": "hi"}, "prompt_eval_count": 1, "eval_count": 1}
    )
    respx.post("http://ollama.test/api/embed").respond(
        json={"embeddings": [[0.1, 0.2]]}
    )

    resp = router.complete("extraction", request())
    assert resp.provider == "ollama" and resp.text == "hi"
    vecs = router.embed(["x"])
    assert vecs == [[0.1, 0.2]]
    # openrouter was never constructed — the lazy factory for it never ran.
    assert "openrouter" not in router._providers
