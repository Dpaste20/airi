import asyncio
import json
import os
import shutil
import subprocess
from contextlib import asynccontextmanager
from typing import Optional
from pathlib import Path
from urllib.parse import urlparse
import emoji
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agno.agent import Agent
from agno.models.ollama import Ollama

from agno.tools.youtube import YouTubeTools
from agno.tools.shell import ShellTools
from toolkits.systemTools import SystemTools
from toolkits.bashTools import BashTools
from agno.skills import Skills, LocalSkills
from agno.db.sqlite import SqliteDb

from router import route

config = json.loads(Path(__file__).with_name("agentConfig.json").read_text())

# ---- ASR (speech.cpp `speech serve`) ----
SPEECH_URL = os.environ.get("SPEECH_URL", "http://127.0.0.1:5000")
ASR_MODEL = config.get("asr_model", "qwen3-asr-0.6b")
ASR_FIELDS = {
    k: v
    for k, v in {
        "language": config.get("asr_language"),
        "prompt": ", ".join(config.get("asr_hotwords", [])),
    }.items()
    if v
}
_speech_proc: Optional[subprocess.Popen] = None


class OllamaThinking(Ollama):
    """Agno's Ollama adapter ignores Ollama's native `thinking` field."""

    @staticmethod
    def _thinking(response) -> str:
        try:
            message = response["message"]
        except (KeyError, TypeError, AttributeError):
            message = getattr(response, "message", None)
        if message is None:
            return ""
        thinking = (
            message.get("thinking")
            if hasattr(message, "get")
            else getattr(message, "thinking", None)
        )
        return thinking or ""

    def _parse_provider_response(self, response):
        model_response = super()._parse_provider_response(response)
        thinking = self._thinking(response)
        if thinking:
            model_response.reasoning_content = (model_response.reasoning_content or "") + thinking
        return model_response

    def _parse_provider_response_delta(self, response):
        model_response = super()._parse_provider_response_delta(response)
        thinking = self._thinking(response)
        if thinking:
            model_response.reasoning_content = (model_response.reasoning_content or "") + thinking
        return model_response


def build_model(spec: str):
    if spec.startswith("ollama:"):
        return OllamaThinking(id=spec.split(":", 1)[1])
    return spec


system_tools = SystemTools()

agent = Agent(
    model=build_model(config["model"]),
    tools=[YouTubeTools(), system_tools , ShellTools()],
    db=SqliteDb(db_file="data/agent.db"),
    add_history_to_context=True,
    skills=Skills(loaders=[LocalSkills(str(Path(__file__).parent / "skills"))]),
    system_message=(Path(__file__).parent / "core-directives" / "SystemMessage.md").read_text(),
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
    global _speech_proc
    # Spawn the ASR backend unless SPEECH_URL points at one you run yourself.
    if not os.environ.get("SPEECH_URL") and shutil.which("speech"):
        port = urlparse(SPEECH_URL).port or 8081
        _speech_proc = subprocess.Popen(
            ["speech", "serve", ASR_MODEL, "--host", "127.0.0.1", "--port", str(port)]
        )
    yield
    if _speech_proc:
        _speech_proc.terminate()
        try:
            _speech_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _speech_proc.kill()
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


@app.post("/transcribe")
async def transcribe(request: Request):
    """Raw audio/wav body in, {"text": ...} out. Proxies to speech.cpp."""
    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=400, detail="empty body")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{SPEECH_URL}/v1/audio/transcriptions",
                files={"file": ("speech.wav", audio, "audio/wav")},
                data=ASR_FIELDS or None,
            )
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ASR backend: {e}")
    return {"text": (r.json().get("text") or "").strip()}


@app.post("/audio/stop")
async def audio_stop():
    """Barge-in: the TUI calls this when recording starts so Airi stops talking."""
    await asyncio.to_thread(stop_audio)
    return {"ok": True}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Return the full response in one JSON payload."""
    system_tools.clear_pending()
    try:
        # d1 decision model: pick a tool (auto-run no-arg tools, or hint the agent)
        message, _ = await route(req.message, system_tools)
        result = await agent.arun(
            message,
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
            # d1 decision model: pick a tool (auto-run no-arg tools, or hint the agent)
            message, decision = await route(req.message, system_tools)
            if decision:
                # Extra SSE field; the TUI currently ignores it.
                yield f"data: {json.dumps({'route': decision})}\n\n"

            async for chunk in agent.arun(
                message,
                stream=True,
                session_id=req.session_id,
                user_id=req.user_id,
            ):
                # Reasoning arrives as its own delta; the TUI renders it dimmed above the answer.
                thinking = getattr(chunk, "reasoning_content", None)
                if isinstance(thinking, str) and thinking:
                    yield f"data: {json.dumps({'thinking': thinking})}\n\n"

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
