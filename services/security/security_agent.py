"""
AIOS Security Agent.
For authorized security auditing, vulnerability assessment, and network analysis.
All operations require explicit scope confirmation.
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


class SecurityAgent(ITAgent):
    """
    Security-focused agent for authorized penetration testing and auditing.
    IMPORTANT: Only use on systems you have explicit permission to test.
    """

    SYSTEM = """\
You are AIOS Security Agent — an expert in cybersecurity, network analysis, and system hardening.
You assist with authorized security auditing, vulnerability assessment, log analysis,
firewall configuration, and incident response.

CRITICAL: Always confirm the scope and authorization before running offensive tools.
Never perform unauthorized scans or attacks. Document everything.

Tools: nmap, nikto, lynis, fail2ban, UFW, auditd, journalctl, ss, netstat.

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
            name="audit_system",
            description="Run Lynis system security audit",
            parameters=_schema({
                "quick": {"type": "boolean", "default": True},
            }),
            handler=self._audit_system,
        ))
        self.register_tool(ToolDefinition(
            name="check_open_ports",
            description="Check locally open ports and listening services",
            parameters=_schema({}),
            handler=self._open_ports,
        ))
        self.register_tool(ToolDefinition(
            name="check_failed_logins",
            description="Show recent failed SSH and login attempts",
            parameters=_schema({
                "lines": {"type": "integer", "default": 50},
            }),
            handler=self._failed_logins,
        ))
        self.register_tool(ToolDefinition(
            name="firewall_status",
            description="Show UFW firewall rules and status",
            parameters=_schema({}),
            handler=self._firewall_status,
        ))
        self.register_tool(ToolDefinition(
            name="firewall_rule",
            description="Add or delete a UFW firewall rule",
            parameters=_schema({
                "action": {"type": "string", "enum": ["allow", "deny", "delete"]},
                "port": {"type": "string"},
                "protocol": {"type": "string", "enum": ["tcp", "udp", "any"], "default": "tcp"},
                "from_ip": {"type": "string"},
            }, ["action", "port"]),
            handler=self._firewall_rule,
        ))
        self.register_tool(ToolDefinition(
            name="check_processes_for_malware",
            description="Check for suspicious processes, unusual network connections",
            parameters=_schema({}),
            handler=self._check_processes,
        ))
        self.register_tool(ToolDefinition(
            name="analyze_logs",
            description="Analyze system logs for anomalies",
            parameters=_schema({
                "log_path": {"type": "string", "default": "/var/log/syslog"},
                "keyword": {"type": "string"},
                "lines": {"type": "integer", "default": 200},
            }),
            handler=self._analyze_logs,
        ))
        self.register_tool(ToolDefinition(
            name="check_file_integrity",
            description="Check file integrity using hashes or AIDE",
            parameters=_schema({
                "path": {"type": "string"},
                "recursive": {"type": "boolean", "default": False},
            }, ["path"]),
            handler=self._file_integrity,
        ))

    @staticmethod
    async def _audit_system(quick: bool = True) -> dict:
        flag = "--quick" if quick else ""
        return await shell_exec(f"lynis audit system {flag} --no-colors 2>&1", timeout=300)

    @staticmethod
    async def _open_ports() -> dict:
        ss_result = await shell_exec("ss -tlunp")
        netstat_result = await shell_exec("netstat -tlunp 2>/dev/null || echo 'netstat not available'")
        return {
            "ss": ss_result["stdout"],
            "netstat": netstat_result["stdout"],
        }

    @staticmethod
    async def _failed_logins(lines: int = 50) -> dict:
        auth_log = await shell_exec(f"grep -i 'failed\\|invalid\\|refused' /var/log/auth.log 2>/dev/null | tail -{lines}")
        journalctl = await shell_exec(f"journalctl _SYSTEMD_UNIT=sshd.service --no-pager | grep -i failed | tail -{lines}")
        return {
            "auth_log": auth_log["stdout"],
            "journalctl": journalctl["stdout"],
        }

    @staticmethod
    async def _firewall_status() -> dict:
        return await shell_exec("ufw status verbose")

    @staticmethod
    async def _firewall_rule(action: str, port: str, protocol: str = "tcp",
                             from_ip: str | None = None) -> dict:
        import shlex
        from_flag = f"from {shlex.quote(from_ip)}" if from_ip else ""
        proto = "" if protocol == "any" else f"/{protocol}"
        return await shell_exec(f"ufw {action} {from_flag} {shlex.quote(port)}{proto}")

    @staticmethod
    async def _check_processes() -> dict:
        unusual_net = await shell_exec("ss -tnp | grep ESTABLISHED | grep -v 'sshd\\|chrome\\|firefox\\|curl'")
        hidden_procs = await shell_exec("ps aux | awk '{print $11}' | sort | uniq -c | sort -rn | head -30")
        suid_files = await shell_exec("find / -perm -4000 -type f 2>/dev/null | head -30")
        return {
            "unusual_connections": unusual_net["stdout"],
            "process_count": hidden_procs["stdout"],
            "suid_files": suid_files["stdout"],
        }

    @staticmethod
    async def _analyze_logs(log_path: str = "/var/log/syslog",
                            keyword: str | None = None, lines: int = 200) -> dict:
        import shlex
        grep = f"| grep {shlex.quote(keyword)}" if keyword else ""
        return await shell_exec(f"tail -{lines} {shlex.quote(log_path)} {grep}")

    @staticmethod
    async def _file_integrity(path: str, recursive: bool = False) -> dict:
        import shlex
        if recursive:
            return await shell_exec(f"find {shlex.quote(path)} -type f -exec sha256sum {{}} \\;", timeout=120)
        return await shell_exec(f"sha256sum {shlex.quote(path)}")
