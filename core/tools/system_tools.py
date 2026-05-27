"""
Built-in system tools for AIOS agents.
Covers: shell, file ops, Docker, K8s, git, network scanning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("aios.tools.system")


# ─── Shell ────────────────────────────────────────────────────────────────────

async def shell_exec(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 60,
    capture_stderr: bool = True,
) -> dict:
    """Execute a shell command and return stdout, stderr, exit code."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE if capture_stderr else asyncio.subprocess.DEVNULL,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace") if capture_stderr else "",
            "exit_code": proc.returncode,
            "command": command,
        }
    except asyncio.TimeoutError:
        return {"error": f"Command timed out after {timeout}s", "command": command, "exit_code": -1}
    except Exception as exc:
        return {"error": str(exc), "command": command, "exit_code": -1}


# ─── File Operations ──────────────────────────────────────────────────────────

async def file_read(path: str, max_lines: int = 500) -> dict:
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return {"error": f"File not found: {path}"}
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        truncated = len(lines) > max_lines
        return {
            "content": "\n".join(lines[:max_lines]),
            "lines_total": len(lines),
            "truncated": truncated,
            "path": str(p),
        }
    except Exception as exc:
        return {"error": str(exc)}


async def file_write(path: str, content: str, append: bool = False) -> dict:
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        p.write_text(content, encoding="utf-8") if not append else open(p, "a").write(content)
        return {"success": True, "path": str(p), "bytes": len(content.encode())}
    except Exception as exc:
        return {"error": str(exc)}


async def file_list(path: str, pattern: str = "*", recursive: bool = False) -> dict:
    try:
        p = Path(path).expanduser()
        if not p.is_dir():
            return {"error": f"Not a directory: {path}"}
        glob_fn = p.rglob if recursive else p.glob
        entries = []
        for item in sorted(glob_fn(pattern))[:200]:
            entries.append({
                "name": item.name,
                "path": str(item),
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            })
        return {"entries": entries, "count": len(entries)}
    except Exception as exc:
        return {"error": str(exc)}


# ─── Docker ───────────────────────────────────────────────────────────────────

async def docker_ps(all_containers: bool = False) -> dict:
    cmd = "docker ps --format json" + (" -a" if all_containers else "")
    result = await shell_exec(cmd)
    if result["exit_code"] != 0:
        return {"error": result["stderr"]}
    containers = []
    for line in result["stdout"].strip().splitlines():
        try:
            containers.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"containers": containers, "count": len(containers)}


async def docker_logs(container: str, tail: int = 100) -> dict:
    result = await shell_exec(f"docker logs --tail {tail} {shlex.quote(container)}")
    return {"logs": result["stdout"], "stderr": result["stderr"], "container": container}


async def docker_exec(container: str, command: str) -> dict:
    result = await shell_exec(f"docker exec {shlex.quote(container)} {command}")
    return result


async def docker_stats() -> dict:
    result = await shell_exec("docker stats --no-stream --format json")
    if result["exit_code"] != 0:
        return {"error": result["stderr"]}
    stats = []
    for line in result["stdout"].strip().splitlines():
        try:
            stats.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"stats": stats}


async def docker_compose_up(path: str, detach: bool = True) -> dict:
    flags = "-d" if detach else ""
    result = await shell_exec(f"docker compose {flags} up", cwd=path, timeout=120)
    return result


async def docker_compose_down(path: str) -> dict:
    result = await shell_exec("docker compose down", cwd=path, timeout=60)
    return result


# ─── Kubernetes ───────────────────────────────────────────────────────────────

async def kubectl_get(resource: str, namespace: str = "default", output: str = "json") -> dict:
    cmd = f"kubectl get {shlex.quote(resource)} -n {shlex.quote(namespace)} -o {output}"
    result = await shell_exec(cmd)
    if result["exit_code"] != 0:
        return {"error": result["stderr"]}
    if output == "json":
        try:
            return json.loads(result["stdout"])
        except json.JSONDecodeError:
            return {"raw": result["stdout"]}
    return {"output": result["stdout"]}


async def kubectl_describe(resource: str, name: str, namespace: str = "default") -> dict:
    cmd = f"kubectl describe {shlex.quote(resource)} {shlex.quote(name)} -n {shlex.quote(namespace)}"
    result = await shell_exec(cmd)
    return {"output": result["stdout"], "error": result["stderr"]}


async def kubectl_logs(pod: str, namespace: str = "default",
                       container: Optional[str] = None, tail: int = 100) -> dict:
    c_flag = f"-c {shlex.quote(container)}" if container else ""
    cmd = f"kubectl logs {shlex.quote(pod)} -n {shlex.quote(namespace)} {c_flag} --tail={tail}"
    result = await shell_exec(cmd)
    return {"logs": result["stdout"], "error": result["stderr"]}


async def kubectl_apply(manifest_path: str) -> dict:
    result = await shell_exec(f"kubectl apply -f {shlex.quote(manifest_path)}", timeout=120)
    return result


# ─── Git ──────────────────────────────────────────────────────────────────────

async def git_status(repo_path: str = ".") -> dict:
    result = await shell_exec("git status --porcelain", cwd=repo_path)
    return {"status": result["stdout"], "exit_code": result["exit_code"]}


async def git_log(repo_path: str = ".", n: int = 20) -> dict:
    fmt = "--format=%H|%an|%ae|%ar|%s"
    result = await shell_exec(f"git log -n {n} {fmt}", cwd=repo_path)
    commits = []
    for line in result["stdout"].strip().splitlines():
        parts = line.split("|", 4)
        if len(parts) == 5:
            commits.append({
                "hash": parts[0],
                "author": parts[1],
                "email": parts[2],
                "relative": parts[3],
                "subject": parts[4],
            })
    return {"commits": commits}


async def git_diff(repo_path: str = ".", staged: bool = False) -> dict:
    flag = "--staged" if staged else ""
    result = await shell_exec(f"git diff {flag}", cwd=repo_path)
    return {"diff": result["stdout"]}


async def git_clone(url: str, destination: str, depth: Optional[int] = None) -> dict:
    depth_flag = f"--depth {depth}" if depth else ""
    result = await shell_exec(
        f"git clone {depth_flag} {shlex.quote(url)} {shlex.quote(destination)}",
        timeout=300,
    )
    return result


# ─── Network ──────────────────────────────────────────────────────────────────

async def network_ping(host: str, count: int = 4) -> dict:
    result = await shell_exec(f"ping -c {count} {shlex.quote(host)}", timeout=30)
    return {"output": result["stdout"], "exit_code": result["exit_code"]}


async def network_nmap(target: str, flags: str = "-sV --top-ports 100") -> dict:
    """Basic nmap scan. Requires nmap installed and appropriate privileges."""
    safe_flags = flags.replace(";", "").replace("&&", "").replace("|", "")
    result = await shell_exec(f"nmap {safe_flags} {shlex.quote(target)}", timeout=120)
    return {"output": result["stdout"], "error": result["stderr"]}


async def network_curl(url: str, method: str = "GET",
                       headers: Optional[dict] = None,
                       data: Optional[str] = None,
                       timeout: int = 30) -> dict:
    header_flags = " ".join(f"-H {shlex.quote(f'{k}: {v}')}" for k, v in (headers or {}).items())
    data_flag = f"-d {shlex.quote(data)}" if data else ""
    method_flag = f"-X {shlex.quote(method)}"
    cmd = f"curl -s -i {method_flag} {header_flags} {data_flag} {shlex.quote(url)}"
    result = await shell_exec(cmd, timeout=timeout + 5)
    return {"response": result["stdout"], "exit_code": result["exit_code"]}


# ─── System Info ──────────────────────────────────────────────────────────────

async def system_info() -> dict:
    tasks = {
        "cpu": shell_exec("grep -c ^processor /proc/cpuinfo && cat /proc/loadavg"),
        "memory": shell_exec("free -h"),
        "disk": shell_exec("df -h --output=source,size,used,avail,pcent,target | head -20"),
        "uptime": shell_exec("uptime"),
        "processes": shell_exec("ps aux --sort=-%cpu | head -15"),
        "network_if": shell_exec("ip -brief address"),
    }
    results = {}
    for key, coro in tasks.items():
        r = await coro
        results[key] = r["stdout"].strip()
    return results


async def process_list(filter_name: Optional[str] = None) -> dict:
    cmd = "ps aux --sort=-%cpu"
    if filter_name:
        cmd += f" | grep {shlex.quote(filter_name)}"
    result = await shell_exec(cmd)
    return {"output": result["stdout"]}


async def service_status(service: str) -> dict:
    result = await shell_exec(f"systemctl status {shlex.quote(service)} --no-pager")
    return {"output": result["stdout"], "active": result["exit_code"] == 0}


async def service_control(service: str, action: str) -> dict:
    if action not in ("start", "stop", "restart", "reload", "enable", "disable"):
        return {"error": f"Invalid action: {action}"}
    result = await shell_exec(f"systemctl {action} {shlex.quote(service)}")
    return result
