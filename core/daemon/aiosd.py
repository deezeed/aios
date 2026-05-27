"""
aiosd — AIOS main daemon.
Central hub: loads config, starts AI router, voice interface, agent pool,
REST API, and event bus. Runs as a systemd service.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # Python < 3.11 backport
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.routing.router import AIRouter, ChatRequest, Message
from core.agents.it_agent import ITAgent
from core.voice.voice_interface import VoiceInterface

logger = logging.getLogger("aios.daemon")

CONFIG_PATH = Path(os.getenv("AIOS_CONFIG", "/etc/aios/aios.toml"))
# parents[2] = core/daemon -> core -> aios (project root)
FALLBACK_CONFIG = Path(__file__).resolve().parents[2] / "config" / "aios.toml"


def load_config() -> dict:
    path = CONFIG_PATH if CONFIG_PATH.exists() else FALLBACK_CONFIG
    with open(path, "rb") as f:
        return tomllib.load(f)


# ─── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(title="AIOS Daemon API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_router: Optional[AIRouter] = None
_it_agent: Optional[ITAgent] = None
_voice: Optional[VoiceInterface] = None
_config: dict = {}


class ChatBody(BaseModel):
    messages: list[dict]
    system: Optional[str] = None
    stream: bool = True
    max_tokens: int = 4096
    temperature: float = 0.7
    force_local: bool = False
    force_cloud: bool = False


class AgentTaskBody(BaseModel):
    task: str
    context: Optional[dict] = None


class SpeakBody(BaseModel):
    text: str
    priority: bool = False


@app.get("/health")
async def health():
    local_ok = await _router._check_local() if _router else False
    return {
        "status": "ok",
        "local_model_available": local_ok,
        "voice_enabled": _config.get("voice", {}).get("enabled", False),
    }


@app.post("/chat")
async def chat(body: ChatBody):
    if not _router:
        return JSONResponse(status_code=503, content={"error": "Daemon not initialized"})
    try:
        messages = [Message(role=m["role"], content=m["content"]) for m in body.messages]
        req = ChatRequest(
            messages=messages,
            system=body.system,
            max_tokens=body.max_tokens,
            temperature=body.temperature,
            stream=False,
            force_local=body.force_local,
            force_cloud=body.force_cloud,
        )
        response = await _router.chat(req)
        return {"response": response}
    except Exception as exc:
        logger.error(f"Chat failed: {exc}")
        msg = str(exc)
        if "ANTHROPIC_API_KEY" in msg or "authentication" in msg.lower() or "api_key" in msg.lower():
            detail = "Chýba ANTHROPIC_API_KEY. Nastav ho: $env:ANTHROPIC_API_KEY='sk-ant-...'"
        elif "connection" in msg.lower() or "connect" in msg.lower():
            detail = "Ollama nie je dostupné a cloud API zlyhalo. Spusti Ollama alebo nastav ANTHROPIC_API_KEY."
        else:
            detail = f"AI router zlyhal: {msg}"
        return JSONResponse(status_code=503, content={"error": detail})


@app.websocket("/chat/stream")
async def chat_stream(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            messages = [Message(role=m["role"], content=m["content"]) for m in data["messages"]]
            req = ChatRequest(
                messages=messages,
                system=data.get("system"),
                max_tokens=data.get("max_tokens", 4096),
                temperature=data.get("temperature", 0.7),
                stream=True,
            )
            async for chunk in _router.stream(req):
                await ws.send_text(chunk)
            await ws.send_json({"done": True})
    except WebSocketDisconnect:
        pass


@app.post("/agent/run")
async def agent_run(body: AgentTaskBody):
    if not _it_agent:
        return JSONResponse(status_code=503, content={"error": "Agent not initialized"})
    try:
        run = await _it_agent.run(body.task, body.context)
        return {
            "id": run.id,
            "status": run.status.value,
            "output": run.final_output,
            "error": run.error,
            "steps": len(run.steps),
            "duration_s": run.duration_seconds,
        }
    except Exception as exc:
        logger.error(f"Agent run failed: {exc}")
        return JSONResponse(status_code=503, content={"error": f"Agent zlyhal: {exc}"})


@app.post("/voice/speak")
async def voice_speak(body: SpeakBody):
    if _voice:
        _voice.speak(body.text, priority=body.priority)
        return {"ok": True}
    return {"ok": False, "error": "Voice not enabled"}


@app.get("/system/info")
async def system_info_endpoint():
    from core.tools.system_tools import system_info
    return await system_info()


@app.get("/docker/ps")
async def docker_ps_endpoint(all: bool = False):
    from core.tools.system_tools import docker_ps
    return await docker_ps(all_containers=all)


@app.get("/k8s/{resource}")
async def k8s_get(resource: str, namespace: str = "default"):
    from core.tools.system_tools import kubectl_get
    return await kubectl_get(resource, namespace)


# ─── Daemon lifecycle ─────────────────────────────────────────────────────────

async def on_voice_command(text: str):
    """Handle a voice command through the AI router."""
    if not _router or not _voice:
        return
    try:
        response = await _router.chat(ChatRequest(
            messages=[Message(role="user", content=text)],
            system=(
                "You are AIOS, an AI-powered operating system assistant. "
                "Answer concisely and clearly. Responses will be spoken aloud, "
                "so avoid markdown, code blocks, or bullet points unless the user asks. "
                "Respond in the same language the user speaks."
            ),
            max_tokens=512,
            temperature=0.5,
        ))
        _voice.speak(response)
        logger.info(f"Voice response: {response[:100]}...")
    except Exception as exc:
        logger.error(f"Voice command handling failed: {exc}")
        _voice.speak("Prepáč, nepodarilo sa spracovať príkaz.")


async def start_daemon():
    global _router, _it_agent, _voice, _config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    _config = load_config()
    logger.info("AIOS daemon starting...")

    _router = AIRouter(_config.get("ai", {}))
    _it_agent = ITAgent("it-agent", _router)

    if _config.get("voice", {}).get("enabled", False):
        loop = asyncio.get_event_loop()
        _voice = VoiceInterface(_config["voice"], on_command=on_voice_command)
        _voice.start(loop)
        logger.info("Voice interface started")

    host = os.getenv("AIOS_HOST", "127.0.0.1")
    port = int(os.getenv("AIOS_PORT", "7474"))

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    logger.info(f"AIOS API listening on http://{host}:{port}")

    def handle_signal(sig, frame):
        logger.info(f"Signal {sig}, shutting down...")
        asyncio.get_event_loop().create_task(shutdown())

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    await server.serve()


async def shutdown():
    global _router, _voice
    if _voice:
        _voice.stop()
    if _router:
        await _router.close()
    logger.info("AIOS daemon stopped")


if __name__ == "__main__":
    asyncio.run(start_daemon())
