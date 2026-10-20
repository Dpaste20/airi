import asyncio
import json
import subprocess
from contextlib import asynccontextmanager
from typing import Optional

import emoji
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio, os, tempfile, wave
from fastapi import Request
from agno.agent import Agent

from agno.tools.youtube import YouTubeTools
from toolkits.systemTools import SystemTools


from pathlib import Path

config = json.loads(Path(__file__).with_name("agentConfig.json").read_text())

system_tools = SystemTools()

agent = Agent(
    model=config["model"],
    tools=[YouTubeTools(), system_tools],
    system_message=config["system_message"],
)


def speak_response(text: str) -> Optional[subprocess.Popen]:
    """Speak the text. -w makes spd-say block until speech ends, so the returned
    process can be waited on."""
    stop_audio()

    clean_text = emoji.replace_emoji(text, replace="")

    try:
        return subprocess.Popen(["spd-say", "-w", "--", clean_text])
    except Exception as e:
        print(f"Error executing spd-say: {e}")
        return None


_background: set[asyncio.Task] = set()


async def _finish_response(text: str):
    """Reply fully delivered + spoken -> then run any queued power action (3s later)."""
    proc = speak_response(text) if text else None
    if proc:
        await asyncio.to_thread(proc.wait)
    system_tools.run_pending(delay=3)


def finish_response(text: str):
    # Fire-and-forget so the HTTP/SSE response isn't held open while speaking.
    task = asyncio.create_task(_finish_response(text))
    _background.add(task)
    task.add_done_callback(_background.discard)


def stop_audio():
    try:
        subprocess.run(["spd-say", "-C"], check=False)
    except Exception as e:
        print(f"Error stopping audio: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    stop_audio()


app = FastAPI(title="Airi Agent API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    return {"status": "ok", "agent": config.get("name", "Airi"), "model": config["model"]}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Return the full response in one JSON payload."""
    system_tools.clear_pending()
    try:
        result = await agent.arun(
            req.message,
            session_id=req.session_id,
            user_id=req.user_id,
        )
    except Exception as e:
        system_tools.clear_pending()
        raise HTTPException(status_code=500, detail=str(e))

    response_text = str(result.content or "")
    finish_response(response_text)

    return ChatResponse(
        response=response_text,
        session_id=req.session_id,
    )


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """Stream the response as Server-Sent Events."""

    async def event_generator():
        full_text = ""
        system_tools.clear_pending()
        try:
            async for chunk in agent.arun(
                req.message,
                stream=True,
                session_id=req.session_id,
                user_id=req.user_id,
            ):
                text = getattr(chunk, "content", None)
                if isinstance(text, str) and text:
                    full_text += text
                    yield f"data: {json.dumps({'delta': text})}\n\n"
            yield "data: [DONE]\n\n"
            finish_response(full_text)
        except Exception as e:
            system_tools.clear_pending()
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000)
