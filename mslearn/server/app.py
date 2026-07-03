from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from mslearn.providers.base import ProviderBadOutputError
from mslearn.server.routers import admin, chat, corpus, evals, exports, memory, study
from mslearn.worker.context import PipelineContext, build_default_context, set_context


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.context is None:
        ctx = build_default_context()
        set_context(ctx)
        app.state.context = ctx
    yield


def create_app(context: PipelineContext | None = None) -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.state.context = context
    if context is not None:
        set_context(context)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.exception_handler(ProviderBadOutputError)
    async def provider_bad_output_handler(_request, exc: ProviderBadOutputError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    app.include_router(admin.router)
    app.include_router(chat.router)
    app.include_router(corpus.router)
    app.include_router(exports.router)
    app.include_router(memory.router)
    app.include_router(study.router)
    app.include_router(study.quiz_router)
    app.include_router(evals.router)
    dist = Path("frontend") / "dist"
    if dist.exists():
        app.mount("/", _SPAStaticFiles(directory=dist, html=True), name="frontend")
    return app


class _SPAStaticFiles(StaticFiles):
    """Serve index.html for unknown non-API paths so client-side routes survive refresh."""

    async def get_response(self, path: str, scope):
        from starlette.exceptions import HTTPException as StarletteHTTPException

        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not path.startswith("api"):
                return await super().get_response("index.html", scope)
            raise
