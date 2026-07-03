import logging
import time
from dataclasses import replace
from typing import Callable, Iterator

from mslearn.opsdb import OpsDB
from mslearn.profiles import ProfilesConfig, RoleConfig, get_active_profile_name
from mslearn.providers.base import ModelProvider, ModelRequest, ModelResponse, ProviderError
from mslearn.providers.claude_code import ClaudeCodeProvider
from mslearn.providers.ollama import OllamaProvider
from mslearn.providers.openrouter import OpenRouterProvider
from mslearn.settings import Settings

logger = logging.getLogger(__name__)


class ModelRouter:
    def __init__(
        self,
        cfg: ProfilesConfig,
        db: OpsDB,
        settings: Settings,
        providers: dict[str, ModelProvider] | None = None,
    ):
        self._cfg = cfg
        self._db = db
        # Providers are built lazily, on first use per name, and cached here.
        # OpenRouterProvider.__init__ raises when the API key is empty — an
        # eager dict literal here (the old behaviour) meant the app could
        # never boot keyless, even on the `offline` profile which never
        # touches openrouter at all. `providers` (tests) still fully
        # pre-populates this dict, bypassing the factories entirely.
        self._providers: dict[str, ModelProvider] = dict(providers) if providers else {}
        self._factories: dict[str, Callable[[], ModelProvider]] = {
            "ollama": lambda: OllamaProvider(settings.ollama_base_url),
            "openrouter": lambda: OpenRouterProvider(settings.openrouter_api_key),
            "claude_code": lambda: ClaudeCodeProvider(settings.claude_binary),
        }

    def _provider(self, name: str) -> ModelProvider:
        if name not in self._providers:
            self._providers[name] = self._factories[name]()
        return self._providers[name]

    def _resolve(self, role: str) -> tuple[ModelProvider, RoleConfig]:
        profile = self._cfg.profiles[get_active_profile_name(self._db, self._cfg)]
        role_cfg = profile.roles[role]
        return self._provider(role_cfg.provider), role_cfg

    def _merged(self, request: ModelRequest, role_cfg: RoleConfig) -> ModelRequest:
        if not role_cfg.params:
            return request
        return replace(request, params={**role_cfg.params, **request.params})

    def complete(self, role: str, request: ModelRequest) -> ModelResponse:
        provider, role_cfg = self._resolve(role)
        try:
            resp = provider.complete(role_cfg.model, self._merged(request, role_cfg))
        except ProviderError as exc:
            self._db.log_model_call(role=role, provider=provider.name, model=role_cfg.model,
                                    outcome="error", error=str(exc)[:500])
            logger.warning("provider error role=%s model=%s: %s", role, role_cfg.model, str(exc)[:120])
            raise
        except Exception as exc:
            self._db.log_model_call(role=role, provider=provider.name, model=role_cfg.model,
                                    outcome="error", error=str(exc)[:500])
            logger.warning("provider error role=%s model=%s: %s", role, role_cfg.model, str(exc)[:120])
            raise
        self._db.log_model_call(
            role=role, provider=provider.name, model=role_cfg.model,
            input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
            latency_ms=resp.latency_ms, cost_usd=resp.cost_usd, outcome="ok",
        )
        return resp

    def stream(self, role: str, request: ModelRequest) -> Iterator[str]:
        provider, role_cfg = self._resolve(role)
        start = time.perf_counter()
        outcome, error = "ok", None
        try:
            yield from provider.stream(role_cfg.model, self._merged(request, role_cfg))
        except ProviderError as exc:
            outcome, error = "error", str(exc)[:500]
            logger.warning("provider error role=%s model=%s: %s", role, role_cfg.model, str(exc)[:120])
            raise
        except GeneratorExit:
            outcome = "abandoned"
            raise
        except Exception as exc:
            outcome, error = "error", str(exc)[:500]
            logger.warning("provider error role=%s model=%s: %s", role, role_cfg.model, str(exc)[:120])
            raise
        finally:
            self._db.log_model_call(
                role=role, provider=provider.name, model=role_cfg.model,
                latency_ms=(time.perf_counter() - start) * 1000,
                outcome=outcome, error=error,
            )

    def embed(self, texts: list[str]) -> list[list[float]]:
        provider, role_cfg = self._resolve("embedding")
        start = time.perf_counter()
        try:
            vectors = provider.embed(role_cfg.model, texts)
        except ProviderError as exc:
            self._db.log_model_call(role="embedding", provider=provider.name,
                                    model=role_cfg.model, outcome="error", error=str(exc)[:500])
            logger.warning("provider error role=embedding model=%s: %s", role_cfg.model, str(exc)[:120])
            raise
        except Exception as exc:
            self._db.log_model_call(role="embedding", provider=provider.name,
                                    model=role_cfg.model, outcome="error", error=str(exc)[:500])
            logger.warning("provider error role=embedding model=%s: %s", role_cfg.model, str(exc)[:120])
            raise
        self._db.log_model_call(
            role="embedding", provider=provider.name, model=role_cfg.model,
            latency_ms=(time.perf_counter() - start) * 1000, outcome="ok",
        )
        return vectors
