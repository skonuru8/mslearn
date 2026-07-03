from contextlib import asynccontextmanager

from fastapi import FastAPI

from mslearn.server.routers import admin, corpus
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

    app.include_router(admin.router)
    app.include_router(corpus.router)
    return app
