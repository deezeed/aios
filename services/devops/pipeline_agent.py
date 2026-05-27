"""
AIOS DevOps Pipeline Agent.
Specializes in CI/CD, Docker, Kubernetes, and infrastructure automation.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.agents.it_agent import ITAgent
from core.tools.system_tools import shell_exec
from core.agents.base import ToolDefinition


def _schema(props: dict, req: list | None = None) -> dict:
    return {"type": "object", "properties": props, "required": req or []}


class DevOpsAgent(ITAgent):
    """
    DevOps-specialized agent with extra CI/CD, Terraform, and Ansible tools.
    Inherits all IT tools and adds pipeline-specific capabilities.
    """

    SYSTEM = """\
You are AIOS DevOps Agent — an expert in CI/CD pipelines, containerization, and infrastructure.
You automate deployments, manage Kubernetes clusters, write Dockerfiles and Helm charts,
and help with Terraform and Ansible. You have full system access.

Think before acting. For production changes, state what you're about to do and why.
Prefer idempotent operations. Use Infrastructure as Code principles.

Output tool_call blocks to use tools:
```tool_call
{"name": "tool_name", "arguments": {...}}
```
"""

    @property
    def system_prompt(self) -> str:
        return self.SYSTEM

    def setup_tools(self):
        super().setup_tools()
        self.register_tool(ToolDefinition(
            name="terraform_plan",
            description="Run terraform plan in a directory",
            parameters=_schema({
                "path": {"type": "string", "description": "Directory containing terraform files"},
                "vars": {"type": "object", "description": "Terraform variables"},
            }, ["path"]),
            handler=self._terraform_plan,
        ))
        self.register_tool(ToolDefinition(
            name="terraform_apply",
            description="Run terraform apply (non-interactive) in a directory",
            parameters=_schema({
                "path": {"type": "string"},
                "auto_approve": {"type": "boolean", "default": False},
            }, ["path"]),
            handler=self._terraform_apply,
        ))
        self.register_tool(ToolDefinition(
            name="ansible_run",
            description="Run an Ansible playbook",
            parameters=_schema({
                "playbook": {"type": "string"},
                "inventory": {"type": "string"},
                "extra_vars": {"type": "object"},
                "dry_run": {"type": "boolean", "default": True},
            }, ["playbook"]),
            handler=self._ansible_run,
        ))
        self.register_tool(ToolDefinition(
            name="helm_deploy",
            description="Deploy or upgrade a Helm chart",
            parameters=_schema({
                "release": {"type": "string"},
                "chart": {"type": "string"},
                "namespace": {"type": "string", "default": "default"},
                "values_file": {"type": "string"},
                "dry_run": {"type": "boolean", "default": True},
            }, ["release", "chart"]),
            handler=self._helm_deploy,
        ))
        self.register_tool(ToolDefinition(
            name="build_docker_image",
            description="Build a Docker image from a Dockerfile",
            parameters=_schema({
                "context": {"type": "string"},
                "tag": {"type": "string"},
                "dockerfile": {"type": "string", "default": "Dockerfile"},
                "build_args": {"type": "object"},
            }, ["context", "tag"]),
            handler=self._build_docker,
        ))

    @staticmethod
    async def _terraform_plan(path: str, vars: dict | None = None) -> dict:
        var_flags = " ".join(f"-var '{k}={v}'" for k, v in (vars or {}).items())
        return await shell_exec(f"terraform plan {var_flags}", cwd=path, timeout=120)

    @staticmethod
    async def _terraform_apply(path: str, auto_approve: bool = False) -> dict:
        flag = "-auto-approve" if auto_approve else ""
        return await shell_exec(f"terraform apply {flag}", cwd=path, timeout=300)

    @staticmethod
    async def _ansible_run(playbook: str, inventory: str = "inventory",
                           extra_vars: dict | None = None, dry_run: bool = True) -> dict:
        import shlex
        check = "--check" if dry_run else ""
        vars_flag = ""
        if extra_vars:
            import json
            vars_flag = f"--extra-vars '{json.dumps(extra_vars)}'"
        cmd = f"ansible-playbook {check} -i {shlex.quote(inventory)} {vars_flag} {shlex.quote(playbook)}"
        return await shell_exec(cmd, timeout=300)

    @staticmethod
    async def _helm_deploy(release: str, chart: str, namespace: str = "default",
                           values_file: str | None = None, dry_run: bool = True) -> dict:
        import shlex
        dry_flag = "--dry-run" if dry_run else ""
        values_flag = f"-f {shlex.quote(values_file)}" if values_file else ""
        cmd = (f"helm upgrade --install {dry_flag} {shlex.quote(release)} {shlex.quote(chart)} "
               f"-n {shlex.quote(namespace)} {values_flag} --create-namespace")
        return await shell_exec(cmd, timeout=120)

    @staticmethod
    async def _build_docker(context: str, tag: str, dockerfile: str = "Dockerfile",
                            build_args: dict | None = None) -> dict:
        import shlex
        args_flags = " ".join(f"--build-arg {k}={shlex.quote(str(v))}"
                              for k, v in (build_args or {}).items())
        cmd = f"docker build -f {shlex.quote(dockerfile)} -t {shlex.quote(tag)} {args_flags} {shlex.quote(context)}"
        return await shell_exec(cmd, timeout=600)
