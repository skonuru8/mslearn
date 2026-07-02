"""Manual smoke test against live services. Not run in CI.

Usage: .venv/bin/python scripts/smoke_providers.py [role ...]
Defaults to: extraction embedding (local-only). Add e.g. `interactive`
to hit OpenRouter (needs MSL_OPENROUTER_API_KEY in .env).
"""
import sys

from mslearn.opsdb import OpsDB
from mslearn.profiles import load_profiles
from mslearn.providers.base import ModelMessage, ModelRequest
from mslearn.providers.router import ModelRouter
from mslearn.settings import get_settings

roles = sys.argv[1:] or ["extraction", "embedding"]
settings = get_settings()
router = ModelRouter(load_profiles(settings.profiles_path), OpsDB(settings.ops_db), settings)

for role in roles:
    if role == "embedding":
        vecs = router.embed(["hello world"])
        print(f"embedding: OK dim={len(vecs[0])}")
        continue
    resp = router.complete(
        role,
        ModelRequest(
            messages=[ModelMessage(role="user", content="Reply with the single word: pong")],
            max_tokens=16,
        ),
    )
    print(f"{role}: {resp.provider}/{resp.model} -> {resp.text!r} "
          f"({resp.latency_ms:.0f} ms, cost={resp.cost_usd})")
print("Logged calls:", len(OpsDB(settings.ops_db).recent_calls()))
