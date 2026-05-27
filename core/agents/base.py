"""
AIOS Agent Framework — base classes for all agents.
Supports tool calling, multi-step reasoning, sandboxing, and streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Callable, Optional

logger = logging.getLogger("aios.agents")


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict          # JSON Schema
    handler: Callable         # async callable


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    tool_call_id: str
    name: str
    output: Any
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class AgentStep:
    index: int
    thought: Optional[str]
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]
    response: Optional[str]
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AgentRun:
    id: str
    agent_name: str
    task: str
    status: AgentStatus
    steps: list[AgentStep] = field(default_factory=list)
    final_output: Optional[str] = None
    error: Optional[str] = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition):
        self._tools[tool.name] = tool
        logger.debug(f"Tool registered: {tool.name}")

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def get_all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def to_anthropic_format(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in self._tools.values()
        ]

    def to_ollama_format(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]


class BaseAgent(ABC):
    """
    Base class for all AIOS agents.
    Subclass and implement `system_prompt` and optionally override `setup_tools`.
    """

    MAX_STEPS = 20

    def __init__(self, name: str, router, tool_registry: Optional[ToolRegistry] = None):
        self.name = name
        self.router = router
        self.tools = tool_registry or ToolRegistry()
        self._active_runs: dict[str, AgentRun] = {}

        self.setup_tools()

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Return the system prompt that defines this agent's persona and capabilities."""
        ...

    def setup_tools(self):
        """Override to register tools specific to this agent."""
        pass

    def register_tool(self, tool: ToolDefinition):
        self.tools.register(tool)

    async def _execute_tool(self, call: ToolCall) -> ToolResult:
        tool_def = self.tools.get(call.name)
        if not tool_def:
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                output=None,
                error=f"Unknown tool: {call.name}",
            )
        start = asyncio.get_event_loop().time()
        try:
            if asyncio.iscoroutinefunction(tool_def.handler):
                output = await tool_def.handler(**call.arguments)
            else:
                output = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: tool_def.handler(**call.arguments)
                )
            duration = (asyncio.get_event_loop().time() - start) * 1000
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                output=output,
                duration_ms=duration,
            )
        except Exception as exc:
            duration = (asyncio.get_event_loop().time() - start) * 1000
            logger.error(f"Tool {call.name} failed: {exc}", exc_info=True)
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                output=None,
                error=str(exc),
                duration_ms=duration,
            )

    async def run(self, task: str, context: Optional[dict] = None) -> AgentRun:
        run_id = str(uuid.uuid4())
        run = AgentRun(
            id=run_id,
            agent_name=self.name,
            task=task,
            status=AgentStatus.RUNNING,
        )
        self._active_runs[run_id] = run

        logger.info(f"Agent {self.name} starting run {run_id}: {task[:80]}...")

        try:
            messages = []
            if context:
                ctx_str = json.dumps(context, ensure_ascii=False, indent=2)
                messages.append({
                    "role": "user",
                    "content": f"Context:\n```json\n{ctx_str}\n```\n\nTask: {task}",
                })
            else:
                messages.append({"role": "user", "content": task})

            for step_idx in range(self.MAX_STEPS):
                step = await self._do_step(run, step_idx, messages)
                run.steps.append(step)

                if step.tool_calls:
                    run.status = AgentStatus.WAITING_FOR_TOOL
                    results = await asyncio.gather(
                        *[self._execute_tool(tc) for tc in step.tool_calls]
                    )
                    step.tool_results = list(results)
                    run.status = AgentStatus.RUNNING

                    for result in results:
                        content = result.output if result.error is None else f"ERROR: {result.error}"
                        messages.append({
                            "role": "user",
                            "content": f"[Tool result: {result.name}]\n{json.dumps(content, ensure_ascii=False, default=str)}",
                        })
                    continue

                if step.response:
                    run.final_output = step.response
                    break

            run.status = AgentStatus.COMPLETED

        except asyncio.CancelledError:
            run.status = AgentStatus.CANCELLED
        except Exception as exc:
            run.status = AgentStatus.FAILED
            run.error = str(exc)
            logger.error(f"Agent run {run_id} failed: {exc}", exc_info=True)
        finally:
            run.finished_at = datetime.utcnow()
            self._active_runs.pop(run_id, None)

        return run

    async def _do_step(self, run: AgentRun, idx: int, messages: list[dict]) -> AgentStep:
        """Execute one reasoning step. Subclasses can override for custom logic."""
        from ..routing.router import ChatRequest, Message

        chat_messages = [Message(role=m["role"], content=m["content"]) for m in messages]

        response = await self.router.chat(ChatRequest(
            messages=chat_messages,
            system=self.system_prompt,
            max_tokens=4096,
            temperature=0.3,
        ))

        tool_calls = self._parse_tool_calls(response)

        if tool_calls:
            return AgentStep(
                index=idx,
                thought=None,
                tool_calls=tool_calls,
                tool_results=[],
                response=None,
            )

        return AgentStep(
            index=idx,
            thought=None,
            tool_calls=[],
            tool_results=[],
            response=response,
        )

    def _parse_tool_calls(self, response: str) -> list[ToolCall]:
        """Parse tool call JSON blocks from response text."""
        calls = []
        import re
        pattern = r'```tool_call\s*\n(.*?)\n```'
        for match in re.finditer(pattern, response, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                calls.append(ToolCall(
                    id=str(uuid.uuid4()),
                    name=data["name"],
                    arguments=data.get("arguments", {}),
                ))
            except (json.JSONDecodeError, KeyError):
                continue
        return calls

    def cancel(self, run_id: str):
        # Signal cancellation — actual coroutine cancellation handled by caller
        run = self._active_runs.get(run_id)
        if run:
            run.status = AgentStatus.CANCELLED
