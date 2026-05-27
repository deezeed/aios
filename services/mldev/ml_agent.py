"""
AIOS ML Development Agent.
Specializes in AI/ML workflows: training, evaluation, model management, Jupyter, MLflow.
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


class MLAgent(ITAgent):
    """
    ML Development agent for training, evaluation, and model management.
    Can start/stop Jupyter, log to MLflow, manage virtual envs.
    """

    SYSTEM = """\
You are AIOS ML Agent — an expert in machine learning, deep learning, and AI systems.
You help with training models, writing PyTorch/TensorFlow/JAX code, managing experiments,
setting up Jupyter notebooks, and deploying models. You are integrated into the local system.

For large training jobs, check GPU availability first.
Prefer reproducible experiments with proper logging.

Use tool_call blocks:
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
            name="gpu_info",
            description="Get GPU usage and memory info",
            parameters=_schema({}),
            handler=self._gpu_info,
        ))
        self.register_tool(ToolDefinition(
            name="create_venv",
            description="Create a Python virtual environment",
            parameters=_schema({
                "path": {"type": "string"},
                "python_version": {"type": "string", "default": "python3"},
            }, ["path"]),
            handler=self._create_venv,
        ))
        self.register_tool(ToolDefinition(
            name="pip_install",
            description="Install Python packages in a venv or globally",
            parameters=_schema({
                "packages": {"type": "array", "items": {"type": "string"}},
                "venv_path": {"type": "string"},
                "upgrade": {"type": "boolean", "default": False},
            }, ["packages"]),
            handler=self._pip_install,
        ))
        self.register_tool(ToolDefinition(
            name="jupyter_start",
            description="Start a Jupyter notebook server",
            parameters=_schema({
                "port": {"type": "integer", "default": 8888},
                "no_browser": {"type": "boolean", "default": True},
                "notebook_dir": {"type": "string", "default": "."},
            }),
            handler=self._jupyter_start,
        ))
        self.register_tool(ToolDefinition(
            name="run_python_script",
            description="Run a Python script, optionally in a venv",
            parameters=_schema({
                "script": {"type": "string"},
                "args": {"type": "string", "default": ""},
                "venv_path": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout": {"type": "integer", "default": 3600},
            }, ["script"]),
            handler=self._run_python,
        ))
        self.register_tool(ToolDefinition(
            name="ollama_list_models",
            description="List locally available Ollama models",
            parameters=_schema({}),
            handler=self._ollama_list,
        ))
        self.register_tool(ToolDefinition(
            name="ollama_pull",
            description="Pull an Ollama model",
            parameters=_schema({
                "model": {"type": "string"},
            }, ["model"]),
            handler=self._ollama_pull,
        ))
        self.register_tool(ToolDefinition(
            name="ollama_create",
            description="Create a custom Ollama model from a Modelfile",
            parameters=_schema({
                "name": {"type": "string"},
                "modelfile_path": {"type": "string"},
            }, ["name", "modelfile_path"]),
            handler=self._ollama_create,
        ))

    @staticmethod
    async def _gpu_info() -> dict:
        result = await shell_exec("nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader 2>/dev/null || echo 'No NVIDIA GPU'")
        rocm = await shell_exec("rocm-smi 2>/dev/null | head -20 || echo ''")
        return {"nvidia": result["stdout"], "rocm": rocm["stdout"]}

    @staticmethod
    async def _create_venv(path: str, python_version: str = "python3") -> dict:
        return await shell_exec(f"{python_version} -m venv {path}", timeout=60)

    @staticmethod
    async def _pip_install(packages: list[str], venv_path: str | None = None,
                           upgrade: bool = False) -> dict:
        pip = f"{venv_path}/bin/pip" if venv_path else "pip"
        pkgs = " ".join(packages)
        upgrade_flag = "--upgrade" if upgrade else ""
        return await shell_exec(f"{pip} install {upgrade_flag} {pkgs}", timeout=300)

    @staticmethod
    async def _jupyter_start(port: int = 8888, no_browser: bool = True,
                             notebook_dir: str = ".") -> dict:
        nb_flag = "--no-browser" if no_browser else ""
        cmd = f"jupyter notebook {nb_flag} --port={port} --notebook-dir={notebook_dir} &"
        return await shell_exec(cmd)

    @staticmethod
    async def _run_python(script: str, args: str = "", venv_path: str | None = None,
                          cwd: str | None = None, timeout: int = 3600) -> dict:
        import shlex
        python = f"{venv_path}/bin/python" if venv_path else "python3"
        return await shell_exec(f"{python} {shlex.quote(script)} {args}", cwd=cwd, timeout=timeout)

    @staticmethod
    async def _ollama_list() -> dict:
        return await shell_exec("ollama list")

    @staticmethod
    async def _ollama_pull(model: str) -> dict:
        import shlex
        return await shell_exec(f"ollama pull {shlex.quote(model)}", timeout=600)

    @staticmethod
    async def _ollama_create(name: str, modelfile_path: str) -> dict:
        import shlex
        return await shell_exec(f"ollama create {shlex.quote(name)} -f {shlex.quote(modelfile_path)}", timeout=300)
