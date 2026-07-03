from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from mslearn.providers.base import ProviderBadOutputError
from mslearn.server.routers import admin, corpus, study
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
    app.include_router(corpus.router)
    app.include_router(study.router)
    app.include_router(study.quiz_router)
    return app
