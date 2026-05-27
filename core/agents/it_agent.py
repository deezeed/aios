"""
AIOS IT Agent — universal IT operations agent.
Handles DevOps, sysadmin, security, networking, and ML dev tasks.
"""

from __future__ import annotations

from ..tools.system_tools import (
    shell_exec, file_read, file_write, file_list,
    docker_ps, docker_logs, docker_exec, docker_stats,
    docker_compose_up, docker_compose_down,
    kubectl_get, kubectl_describe, kubectl_logs, kubectl_apply,
    git_status, git_log, git_diff, git_clone,
    network_ping, network_nmap, network_curl,
    system_info, process_list, service_status, service_control,
)
from .base import BaseAgent, ToolDefinition, ToolRegistry


def _make_schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
    }


class ITAgent(BaseAgent):
    """
    General-purpose IT agent with full access to system tools.
    Use for DevOps tasks, troubleshooting, automation, and infrastructure management.
    """

    SYSTEM = """\
You are AIOS IT Agent — an expert AI system integrated directly into the operating system.
You have full access to the local system: shell, Docker, Kubernetes, Git, and network tools.

Guidelines:
- Think step-by-step before executing destructive commands
- Always show what you're doing and why
- For production systems, confirm before making changes
- Prefer non-destructive diagnostics first, then fixes
- Use structured output (tables, JSON) when presenting data
- Speak concisely; skip preamble

When you need to run a tool, output a tool_call block:
```tool_call
{"name": "tool_name", "arguments": {...}}
```

Available tools: shell_exec, file_read, file_write, file_list,
docker_ps, docker_logs, docker_exec, docker_stats,
kubectl_get, kubectl_describe, kubectl_logs, kubectl_apply,
git_status, git_log, git_diff, git_clone,
network_ping, network_nmap, network_curl,
system_info, process_list, service_status, service_control
"""

    @property
    def system_prompt(self) -> str:
        return self.SYSTEM

    def setup_tools(self):
        tools = [
            ToolDefinition(
                name="shell_exec",
                description="Execute a shell command on the local system",
                parameters=_make_schema({
                    "command": {"type": "string", "description": "Shell command to run"},
                    "cwd": {"type": "string", "description": "Working directory"},
                    "timeout": {"type": "integer", "default": 60},
                }, ["command"]),
                handler=shell_exec,
            ),
            ToolDefinition(
                name="file_read",
                description="Read a file from the filesystem",
                parameters=_make_schema({
                    "path": {"type": "string"},
                    "max_lines": {"type": "integer", "default": 500},
                }, ["path"]),
                handler=file_read,
            ),
            ToolDefinition(
                name="file_write",
                description="Write or append content to a file",
                parameters=_make_schema({
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "append": {"type": "boolean", "default": False},
                }, ["path", "content"]),
                handler=file_write,
            ),
            ToolDefinition(
                name="file_list",
                description="List files in a directory",
                parameters=_make_schema({
                    "path": {"type": "string"},
                    "pattern": {"type": "string", "default": "*"},
                    "recursive": {"type": "boolean", "default": False},
                }, ["path"]),
                handler=file_list,
            ),
            ToolDefinition(
                name="docker_ps",
                description="List running Docker containers",
                parameters=_make_schema({
                    "all_containers": {"type": "boolean", "default": False},
                }),
                handler=docker_ps,
            ),
            ToolDefinition(
                name="docker_logs",
                description="Get logs from a Docker container",
                parameters=_make_schema({
                    "container": {"type": "string"},
                    "tail": {"type": "integer", "default": 100},
                }, ["container"]),
                handler=docker_logs,
            ),
            ToolDefinition(
                name="docker_exec",
                description="Execute a command inside a Docker container",
                parameters=_make_schema({
                    "container": {"type": "string"},
                    "command": {"type": "string"},
                }, ["container", "command"]),
                handler=docker_exec,
            ),
            ToolDefinition(
                name="docker_stats",
                description="Get CPU/memory stats for all running containers",
                parameters=_make_schema({}),
                handler=docker_stats,
            ),
            ToolDefinition(
                name="docker_compose_up",
                description="Start services defined in docker-compose.yml",
                parameters=_make_schema({
                    "path": {"type": "string", "description": "Directory with docker-compose.yml"},
                    "detach": {"type": "boolean", "default": True},
                }, ["path"]),
                handler=docker_compose_up,
            ),
            ToolDefinition(
                name="docker_compose_down",
                description="Stop and remove docker-compose services",
                parameters=_make_schema({
                    "path": {"type": "string"},
                }, ["path"]),
                handler=docker_compose_down,
            ),
            ToolDefinition(
                name="kubectl_get",
                description="Get Kubernetes resources",
                parameters=_make_schema({
                    "resource": {"type": "string", "description": "e.g. pods, deployments, services"},
                    "namespace": {"type": "string", "default": "default"},
                    "output": {"type": "string", "default": "json"},
                }, ["resource"]),
                handler=kubectl_get,
            ),
            ToolDefinition(
                name="kubectl_logs",
                description="Get logs from a Kubernetes pod",
                parameters=_make_schema({
                    "pod": {"type": "string"},
                    "namespace": {"type": "string", "default": "default"},
                    "container": {"type": "string"},
                    "tail": {"type": "integer", "default": 100},
                }, ["pod"]),
                handler=kubectl_logs,
            ),
            ToolDefinition(
                name="kubectl_apply",
                description="Apply a Kubernetes manifest file",
                parameters=_make_schema({
                    "manifest_path": {"type": "string"},
                }, ["manifest_path"]),
                handler=kubectl_apply,
            ),
            ToolDefinition(
                name="git_status",
                description="Show git repository status",
                parameters=_make_schema({
                    "repo_path": {"type": "string", "default": "."},
                }),
                handler=git_status,
            ),
            ToolDefinition(
                name="git_log",
                description="Show recent git commits",
                parameters=_make_schema({
                    "repo_path": {"type": "string", "default": "."},
                    "n": {"type": "integer", "default": 20},
                }),
                handler=git_log,
            ),
            ToolDefinition(
                name="git_diff",
                description="Show git diff of working tree or staged changes",
                parameters=_make_schema({
                    "repo_path": {"type": "string", "default": "."},
                    "staged": {"type": "boolean", "default": False},
                }),
                handler=git_diff,
            ),
            ToolDefinition(
                name="git_clone",
                description="Clone a git repository",
                parameters=_make_schema({
                    "url": {"type": "string"},
                    "destination": {"type": "string"},
                    "depth": {"type": "integer"},
                }, ["url", "destination"]),
                handler=git_clone,
            ),
            ToolDefinition(
                name="network_ping",
                description="Ping a host to check connectivity",
                parameters=_make_schema({
                    "host": {"type": "string"},
                    "count": {"type": "integer", "default": 4},
                }, ["host"]),
                handler=network_ping,
            ),
            ToolDefinition(
                name="network_nmap",
                description="Run an nmap port scan on a target",
                parameters=_make_schema({
                    "target": {"type": "string"},
                    "flags": {"type": "string", "default": "-sV --top-ports 100"},
                }, ["target"]),
                handler=network_nmap,
            ),
            ToolDefinition(
                name="network_curl",
                description="Make an HTTP request with curl",
                parameters=_make_schema({
                    "url": {"type": "string"},
                    "method": {"type": "string", "default": "GET"},
                    "headers": {"type": "object"},
                    "data": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                }, ["url"]),
                handler=network_curl,
            ),
            ToolDefinition(
                name="system_info",
                description="Get CPU, memory, disk, network, and process info",
                parameters=_make_schema({}),
                handler=system_info,
            ),
            ToolDefinition(
                name="process_list",
                description="List running processes, optionally filtered by name",
                parameters=_make_schema({
                    "filter_name": {"type": "string"},
                }),
                handler=process_list,
            ),
            ToolDefinition(
                name="service_status",
                description="Check systemd service status",
                parameters=_make_schema({
                    "service": {"type": "string"},
                }, ["service"]),
                handler=service_status,
            ),
            ToolDefinition(
                name="service_control",
                description="Start, stop, restart, enable or disable a systemd service",
                parameters=_make_schema({
                    "service": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["start", "stop", "restart", "reload", "enable", "disable"],
                    },
                }, ["service", "action"]),
                handler=service_control,
            ),
        ]
        for t in tools:
            self.register_tool(t)
