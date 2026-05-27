"""
AI Model Router — intelligently routes requests between local Ollama and Claude API.
Strategy: local_first by default, escalates to cloud for complex tasks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Optional

import httpx
import tiktoken
from anthropic import AsyncAnthropic

logger = logging.getLogger("aios.router")


class RouteStrategy(str, Enum):
    LOCAL_FIRST = "local_first"
    CLOUD_FIRST = "cloud_first"
    COST_OPTIMIZED = "cost_optimized"
    SPEED_OPTIMIZED = "speed_optimized"


class TaskComplexity(str, Enum):
    TRIVIAL = "trivial"       # one-liner answers, simple lookups
    SIMPLE = "simple"         # short code, basic Q&A
    MODERATE = "moderate"     # multi-step reasoning, medium code
    COMPLEX = "complex"       # deep analysis, architecture, long code
    CRITICAL = "critical"     # security analysis, prod decisions


@dataclass
class RouteDecision:
    provider: str           # "local" | "cloud"
    model: str
    reason: str
    estimated_cost: float   # USD


@dataclass
class Message:
    role: str               # "user" | "assistant" | "system"
    content: str


@dataclass
class ChatRequest:
    messages: list[Message]
    system: Optional[str] = None
    max_tokens: int = 4096
    temperature: float = 0.7
    stream: bool = True
    task_hint: Optional[TaskComplexity] = None
    force_local: bool = False
    force_cloud: bool = False


@dataclass
class ChatResponse:
    content: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float


class ComplexityEstimator:
    """Estimates task complexity from the prompt to guide routing."""

    COMPLEX_KEYWORDS = {
        "architect", "design", "security audit", "CVE", "exploit",
        "kubernetes", "terraform", "analyze entire", "refactor all",
        "production", "critical", "compliance", "GDPR", "pentest",
        "zero-day", "intrusion", "forensic", "root cause",
    }
    CODE_KEYWORDS = {"```", "def ", "class ", "function", "import ", "#include"}
    SIMPLE_KEYWORDS = {"co je", "what is", "ako", "how to", "explain", "define"}

    def estimate(self, text: str) -> TaskComplexity:
        lower = text.lower()
        token_count = len(text.split())

        if any(kw in lower for kw in self.COMPLEX_KEYWORDS):
            return TaskComplexity.COMPLEX
        if token_count > 500:
            return TaskComplexity.COMPLEX
        if any(kw in text for kw in self.CODE_KEYWORDS) and token_count > 100:
            return TaskComplexity.MODERATE
        if any(kw in lower for kw in self.SIMPLE_KEYWORDS) and token_count < 50:
            return TaskComplexity.TRIVIAL
        if token_count < 30:
            return TaskComplexity.SIMPLE
        return TaskComplexity.MODERATE


class LocalOllamaClient:
    def __init__(self, base_url: str, default_model: str, code_model: str, fast_model: str):
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.code_model = code_model
        self.fast_model = fast_model
        self._client = httpx.AsyncClient(timeout=120.0)

    async def is_available(self) -> bool:
        try:
            r = await self._client.get(f"{self.base_url}/api/tags", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    def pick_model(self, complexity: TaskComplexity, messages: list[Message]) -> str:
        last = " ".join(m.content for m in messages[-3:]).lower()
        has_code = any(k in last for k in ("```", "def ", "class ", "function"))

        if has_code or "code" in last or "script" in last:
            return self.code_model
        if complexity == TaskComplexity.TRIVIAL:
            return self.fast_model
        return self.default_model

    async def chat_stream(self, model: str, messages: list[Message],
                          system: Optional[str], temperature: float,
                          max_tokens: int) -> AsyncIterator[str]:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if system:
            payload["system"] = system

        async with self._client.stream(
            "POST", f"{self.base_url}/api/chat", json=payload
        ) as resp:
            resp.raise_for_status()
            import json as _json
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = _json.loads(line)
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break
                except _json.JSONDecodeError:
                    continue

    async def close(self):
        await self._client.aclose()


class CloudAnthropicClient:
    COST_PER_1K_IN = 0.003    # claude-sonnet-4-6
    COST_PER_1K_OUT = 0.015

    def __init__(self, model: str, fast_model: str):
        self.model = model
        self.fast_model = fast_model
        import os
        self._api_key = os.getenv("ANTHROPIC_API_KEY")
        self._client = AsyncAnthropic(api_key=self._api_key) if self._api_key else None

    def _require_client(self):
        if self._client is None:
            raise RuntimeError(
                "ANTHROPIC_API_KEY nie je nastavený. "
                "Spusti: $env:ANTHROPIC_API_KEY='sk-ant-...'"
            )

    def pick_model(self, complexity: TaskComplexity) -> str:
        if complexity in (TaskComplexity.TRIVIAL, TaskComplexity.SIMPLE):
            return self.fast_model
        return self.model

    async def chat_stream(self, model: str, messages: list[Message],
                          system: Optional[str], temperature: float,
                          max_tokens: int) -> AsyncIterator[str]:
        self._require_client()
        anthropic_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        kwargs: dict = dict(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=anthropic_messages,
        )
        if system:
            kwargs["system"] = system

        async with self._client.messages.stream(**kwargs) as stream:
            async for chunk in stream.text_stream:
                yield chunk


class AIRouter:
    """
    Central AI router for AIOS.
    Routes requests between local Ollama and Claude API based on:
    - Task complexity
    - Local model availability
    - Configured strategy
    - Cost constraints
    """

    def __init__(self, config: dict):
        self.strategy = RouteStrategy(config.get("router", {}).get("strategy", "local_first"))
        self.fallback = config.get("router", {}).get("fallback_to_cloud", True)

        local_cfg = config.get("local", {})
        self.local = LocalOllamaClient(
            base_url=local_cfg.get("base_url", "http://localhost:11434"),
            default_model=local_cfg.get("default_model", "llama3.2:3b"),
            code_model=local_cfg.get("code_model", "qwen2.5-coder:7b"),
            fast_model=local_cfg.get("fast_model", "llama3.2:1b"),
        )

        cloud_cfg = config.get("cloud", {})
        self.cloud = CloudAnthropicClient(
            model=cloud_cfg.get("model", "claude-sonnet-4-6"),
            fast_model=cloud_cfg.get("fast_model", "claude-haiku-4-5-20251001"),
        )

        self.estimator = ComplexityEstimator()
        self._local_available: Optional[bool] = None
        self._last_local_check = 0.0

    async def _check_local(self) -> bool:
        now = time.monotonic()
        if now - self._last_local_check > 30:
            self._local_available = await self.local.is_available()
            self._last_local_check = now
        return self._local_available or False

    def _decide(self, request: ChatRequest, local_ok: bool) -> RouteDecision:
        if request.force_local:
            model = self.local.pick_model(TaskComplexity.MODERATE, request.messages)
            return RouteDecision("local", model, "forced local", 0.0)
        if request.force_cloud:
            complexity = request.task_hint or TaskComplexity.MODERATE
            model = self.cloud.pick_model(complexity)
            return RouteDecision("cloud", model, "forced cloud", 0.002)

        complexity = request.task_hint or self.estimator.estimate(
            " ".join(m.content for m in request.messages[-5:])
        )

        if self.strategy == RouteStrategy.LOCAL_FIRST:
            if local_ok and complexity != TaskComplexity.CRITICAL:
                model = self.local.pick_model(complexity, request.messages)
                return RouteDecision("local", model, f"local_first, complexity={complexity.value}", 0.0)
            if not local_ok and not self.fallback:
                raise RuntimeError("Local model unavailable and cloud fallback disabled")
            model = self.cloud.pick_model(complexity)
            return RouteDecision("cloud", model, "local unavailable, fallback", 0.002)

        if self.strategy == RouteStrategy.CLOUD_FIRST:
            model = self.cloud.pick_model(complexity)
            return RouteDecision("cloud", model, "cloud_first strategy", 0.002)

        if self.strategy == RouteStrategy.COST_OPTIMIZED:
            if local_ok:
                model = self.local.pick_model(complexity, request.messages)
                return RouteDecision("local", model, "cost_optimized: free local", 0.0)
            model = self.cloud.pick_model(TaskComplexity.TRIVIAL)
            return RouteDecision("cloud", model, "cost_optimized: cheapest cloud", 0.0005)

        # SPEED_OPTIMIZED: fastest available
        if local_ok and complexity in (TaskComplexity.TRIVIAL, TaskComplexity.SIMPLE):
            return RouteDecision("local", self.local.fast_model, "speed: local fast", 0.0)
        model = self.cloud.pick_model(complexity)
        return RouteDecision("cloud", model, "speed: cloud", 0.002)

    async def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        local_ok = await self._check_local()
        decision = self._decide(request, local_ok)

        logger.info(f"Route → {decision.provider}/{decision.model} ({decision.reason})")

        try:
            if decision.provider == "local":
                async for chunk in self.local.chat_stream(
                    decision.model, request.messages,
                    request.system, request.temperature, request.max_tokens
                ):
                    yield chunk
            else:
                async for chunk in self.cloud.chat_stream(
                    decision.model, request.messages,
                    request.system, request.temperature, request.max_tokens
                ):
                    yield chunk

        except Exception as exc:
            if decision.provider == "local" and self.fallback:
                logger.warning(f"Local failed ({exc}), falling back to cloud")
                cloud_model = self.cloud.pick_model(TaskComplexity.MODERATE)
                async for chunk in self.cloud.chat_stream(
                    cloud_model, request.messages,
                    request.system, request.temperature, request.max_tokens
                ):
                    yield chunk
            else:
                raise

    async def chat(self, request: ChatRequest) -> str:
        parts: list[str] = []
        async for chunk in self.stream(request):
            parts.append(chunk)
        return "".join(parts)

    async def close(self):
        await self.local.close()
