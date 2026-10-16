import json
import subprocess
from contextlib import asynccontextmanager
from typing import Optional

import emoji
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agno.agent import Agent
from agno.models.ollama import Ollama
from agno.tools.youtube import YouTubeTools
from toolkits.systemTools import SystemTools


from pathlib import Path

config = json.loads(Path(__file__).with_name("agentConfig.json").read_text())

agent = Agent(
    model=config["model"],
    tools=[YouTubeTools(), SystemTools()],
    system_message=config["system_message"],
)


def speak_response(text: str):
    stop_audio()

    clean_text = emoji.replace_emoji(text, replace="")

    try:
        subprocess.Popen(["spd-say", clean_text])
    except Exception as e:
        print(f"Error executing spd-say: {e}")


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
    try:
        result = await agent.arun(
            req.message,
            session_id=req.session_id,
            user_id=req.user_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    response_text = str(result.content or "")
    if response_text:
        speak_response(response_text)

    return ChatResponse(
        response=response_text,
        session_id=req.session_id,
    )


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """Stream the response as Server-Sent Events."""

    async def event_generator():
        full_text = ""
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
            if full_text:
                speak_response(full_text)
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

    uvicorn.run("server:app", host="0.0.0.0", port=8000)
