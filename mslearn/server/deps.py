from fastapi import Request

from mslearn.worker.context import PipelineContext


def get_ctx(request: Request) -> PipelineContext:
    return request.app.state.context
