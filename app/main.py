from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.core.agent import BoundedAgent
from app.core.cache import SemanticCache
from app.core.rag import HybridRAG
from app.core.store import StateStore
from app.models import AgentRequest, AgentResponse

state = StateStore()
cache = SemanticCache()
rag = HybridRAG()
agent = BoundedAgent(rag, state, cache)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await state.initialize()
    yield


app = FastAPI(title="The Unreliable Assistant", version="0.2.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "groq_configured": bool(agent.client),
        "cache_available": cache.available,
    }


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/agent/query", response_model=AgentResponse)
async def query(request: AgentRequest) -> Any:  # AgentResponse, or a 429 JSONResponse
    try:
        response = await agent.handle(request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Agent request failed closed") from exc
    if response.status == "THROTTLED":
        retry_ms = response.retry_after_ms or 1000
        return JSONResponse(
            status_code=429,
            content=response.model_dump(mode="json"),
            headers={"Retry-After": str(max(1, (retry_ms + 999) // 1000))},
        )
    return response


@app.post("/api/v1/agent/stream", response_model=None)
async def stream(request: AgentRequest) -> Any:
    response = await query(request)
    if isinstance(response, JSONResponse):  # throttled: return the 429 as-is, do not stream it
        return response
    answer = response.answer

    async def iterator():
        words = answer.split()
        for index in range(0, len(words), 8):
            yield " ".join(words[index : index + 8]) + " "

    return StreamingResponse(iterator(), media_type="text/plain")
