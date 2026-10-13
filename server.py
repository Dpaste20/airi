import json
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agno.agent import Agent
from agno.models.ollama import Ollama
from agno.tools.youtube import YouTubeTools
from toolkits.systemTools import SystemTools

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
agent = Agent(
    model="ollama:gemma4:31b-cloud",
    tools=[YouTubeTools(), SystemTools()],
    system_message="Your name is Airi",

    markdown=True,
)

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
app = FastAPI(title="Airi Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this in production
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    session_id: Optional[str] = None


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "Airi"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Return the full response in one JSON payload."""
    try:
        result = await agent.arun(
            req.message,
            session_id=req.session_id,
            user_id=req.user_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return ChatResponse(
        response=str(result.content or ""),
        session_id=req.session_id,
    )


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """Stream the response as Server-Sent Events."""

    async def event_generator():
        try:
            async for chunk in agent.arun(
                req.message,
                stream=True,
                session_id=req.session_id,
                user_id=req.user_id,
            ):
                text = getattr(chunk, "content", None)
                if isinstance(text, str) and text:
                    yield f"data: {json.dumps({'delta': text})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
