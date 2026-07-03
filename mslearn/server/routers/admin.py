from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mslearn.opsdb import TUNABLE_DEFAULTS
from mslearn.profiles import get_active_profile_name, load_profiles, set_active_profile_name
from mslearn.server.deps import get_ctx
from mslearn.worker.app import worker_online

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/health")
def health(ctx=Depends(get_ctx)):
    return {
        "api": True,
        "worker": worker_online(),
        "redis": _redis_online(ctx),
        "neo4j": _neo4j_online(ctx),
    }


def _redis_online(ctx) -> bool:
    try:
        import redis

        url = getattr(getattr(ctx, "settings", None), "redis_url", None)
        if not url:
            from mslearn.settings import get_settings

            url = get_settings().redis_url
        client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        return bool(client.ping())
    except Exception:
        return False


def _neo4j_online(ctx) -> bool:
    try:
        ctx.graph.ping()
        return True
    except Exception:
        return False


@router.get("/profiles")
def list_profiles(ctx=Depends(get_ctx)):
    cfg = load_profiles(ctx.settings.profiles_path)
    active = get_active_profile_name(ctx.db, cfg)
    return {"active": active, "available": list(cfg.profiles.keys())}


@router.post("/profiles/{name}")
def switch_profile(name: str, ctx=Depends(get_ctx)):
    cfg = load_profiles(ctx.settings.profiles_path)
    try:
        set_active_profile_name(ctx.db, cfg, name)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"unknown profile {name!r}") from None
    return {"active": name}


@router.get("/tunables")
def list_tunables(ctx=Depends(get_ctx)):
    db = ctx.db
    return [
        {"key": key, "value": db.get_tunable(key), "default": default}
        for key, default in TUNABLE_DEFAULTS.items()
    ]


class TunableUpdate(BaseModel):
    value: float
    reason: str


@router.post("/tunables/{key}")
def set_tunable_endpoint(key: str, body: TunableUpdate, ctx=Depends(get_ctx)):
    try:
        ctx.db.set_tunable(key, body.value, body.reason)
    except KeyError:
        raise HTTPException(status_code=422, detail=f"unknown tunable {key!r}") from None
    return {"key": key, "value": body.value}


@router.get("/tunables/{key}/history")
def tunable_history(key: str, ctx=Depends(get_ctx)):
    if key not in TUNABLE_DEFAULTS:
        raise HTTPException(status_code=422, detail=f"unknown tunable {key!r}")
    return ctx.db.tunable_history(key)


@router.post("/tunables/{key}/rollback")
def rollback_tunable_endpoint(key: str, ctx=Depends(get_ctx)):
    from mslearn.evals.evolve import rollback_tunable

    try:
        value = rollback_tunable(ctx.db, key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"key": key, "value": value}


@router.get("/spend")
def spend(limit: int = 100, ctx=Depends(get_ctx)):
    calls = ctx.db.recent_calls(limit=limit)
    total_cost_usd = sum(c["cost_usd"] for c in calls if c["cost_usd"] is not None)
    by_role: dict[str, int] = {}
    for call in calls:
        by_role[call["role"]] = by_role.get(call["role"], 0) + 1
    return {
        "recent_calls": calls,
        "total_cost_usd": total_cost_usd,
        "total_calls": len(calls),
        "by_role": by_role,
    }
