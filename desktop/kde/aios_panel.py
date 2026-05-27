#!/usr/bin/env python3
"""
AIOS Panel — floating AI assistant + Agent Manager for KDE/Windows.
Tabs: Chat | Agenti | Spustené
"""

from __future__ import annotations

import json
import platform
import sys
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMenu, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSystemTrayIcon, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget,
)

# ─── Constants ────────────────────────────────────────────────────────────────
DAEMON_URL  = "http://127.0.0.1:7474"
PANEL_WIDTH = 430
ACCENT      = "#00bcd4"
BG_DARK     = "#1a1a2e"
BG_MSG      = "#16213e"
BG_USER     = "#0f3460"
BG_CARD     = "#0d1117"
BG_INPUT    = "#0d1117"
AGENTS_FILE = Path.home() / ".config" / "aios" / "agents.json"

AVAILABLE_TOOLS = [
    "shell_exec", "file_read", "file_write", "file_list",
    "docker_ps", "docker_logs", "docker_exec", "docker_stats",
    "kubectl_get", "kubectl_logs", "kubectl_apply",
    "git_status", "git_log", "git_diff", "git_clone",
    "network_ping", "network_nmap", "network_curl",
    "system_info", "process_list", "service_status", "service_control",
]

BUILTIN_AGENTS = [
    {
        "id": "builtin-it",
        "name": "IT Agent",
        "description": "Všeobecný IT agent — shell, Docker, K8s, Git, sieť, systemd",
        "type": "builtin",
        "endpoint": "/agent/run",
    },
    {
        "id": "builtin-devops",
        "name": "DevOps Agent",
        "description": "CI/CD, Terraform, Helm, Ansible, Docker build",
        "type": "builtin",
        "endpoint": "/agent/devops/run",
    },
    {
        "id": "builtin-ml",
        "name": "ML Agent",
        "description": "AI/ML vývoj — Jupyter, Ollama, GPU, venv, pip",
        "type": "builtin",
        "endpoint": "/agent/ml/run",
    },
    {
        "id": "builtin-security",
        "name": "Security Agent",
        "description": "Bezpečnostný audit — Lynis, UFW, logy, porty",
        "type": "builtin",
        "endpoint": "/agent/security/run",
    },
]

BTN = f"""
    QPushButton {{
        background: {BG_CARD};
        color: {ACCENT};
        border: 1px solid #1e2a30;
        border-radius: 6px;
        padding: 5px 12px;
        font-size: 10px;
    }}
    QPushButton:hover {{ background: #1a2535; border-color: {ACCENT}; }}
    QPushButton:pressed {{ background: #263238; }}
    QPushButton:disabled {{ color: #37474f; border-color: #1e2a30; }}
"""
BTN_PRIMARY = f"""
    QPushButton {{
        background: {ACCENT};
        color: #000;
        border: none;
        border-radius: 6px;
        padding: 5px 12px;
        font-weight: bold;
        font-size: 10px;
    }}
    QPushButton:hover {{ background: #00e5ff; }}
    QPushButton:pressed {{ background: #0097a7; }}
    QPushButton:disabled {{ background: #263238; color: #546e7a; }}
"""
BTN_DANGER = """
    QPushButton {
        background: #1a1a2e;
        color: #ef5350;
        border: 1px solid #3a1a1a;
        border-radius: 6px;
        padding: 5px 12px;
        font-size: 10px;
    }
    QPushButton:hover { background: #3a1a1a; }
"""
SCROLL_STYLE = f"""
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: {BG_DARK}; width: 5px; border-radius: 2px; }}
    QScrollBar::handle:vertical {{ background: #263238; border-radius: 2px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
"""


# ─── Workers ──────────────────────────────────────────────────────────────────

class ChatWorker(QThread):
    chunk_received = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, messages: list[dict]):
        super().__init__()
        self.messages = messages

    def run(self):
        try:
            with httpx.Client(timeout=120.0) as c:
                r = c.post(f"{DAEMON_URL}/chat", json={"messages": self.messages, "stream": False})
                if r.status_code == 503:
                    err = r.json().get("error", "AI router nie je dostupný")
                    self.error.emit(err)
                elif r.status_code != 200:
                    self.error.emit(f"Daemon vrátil chybu {r.status_code}: {r.text[:200]}")
                else:
                    self.chunk_received.emit(r.json().get("response", ""))
        except httpx.ConnectError:
            self.error.emit(
                "AIOS daemon nie je spustený.\n\n"
                "Spusti ho:\n  python -m core.daemon.aiosd\n"
                "alebo:  sudo systemctl start aiosd"
            )
        except httpx.TimeoutException:
            self.error.emit("Časový limit — daemon neodpovedá.")
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")
        finally:
            self.finished.emit()


class AgentWorker(QThread):
    output = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, task: str, endpoint: str = "/agent/run", context: dict | None = None):
        super().__init__()
        self.task = task
        self.endpoint = endpoint
        self.context = context

    def run(self):
        try:
            self.output.emit(f"Spúšťam agenta...\nÚloha: {self.task}\n{'─'*40}\n")
            with httpx.Client(timeout=300.0) as c:
                r = c.post(f"{DAEMON_URL}{self.endpoint}",
                           json={"task": self.task, "context": self.context})
                if r.status_code == 503:
                    err = r.json().get("error", "Agent nie je dostupný")
                    self.error.emit(err)
                elif r.status_code != 200:
                    self.error.emit(f"Chyba {r.status_code}: {r.text[:200]}")
                else:
                    self.finished.emit(r.json())
        except httpx.ConnectError:
            self.error.emit("Daemon nie je spustený — sudo systemctl start aiosd")
        except httpx.TimeoutException:
            self.error.emit("Timeout — úloha trvá príliš dlho (>5 min).")
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


# ─── Chat tab ─────────────────────────────────────────────────────────────────

class MessageBubble(QWidget):
    def __init__(self, text: str, is_user: bool):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        role = QLabel("Ty" if is_user else "AIOS")
        role.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        role.setStyleSheet(f"color: {'#90a4ae' if is_user else ACCENT}; background: transparent;")
        self._lbl = QLabel(text)
        self._lbl.setWordWrap(True)
        self._lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._lbl.setFont(QFont("Segoe UI", 10))
        self._lbl.setStyleSheet(f"""
            background: {BG_USER if is_user else BG_MSG};
            color: #eceff1; border-radius: 8px; padding: 8px 12px;
        """)
        lay.addWidget(role)
        lay.addWidget(self._lbl)
        self.setStyleSheet("background: transparent;")

    def update_text(self, t: str):
        self._lbl.setText(t)


class ChatTab(QWidget):
    def __init__(self):
        super().__init__()
        self.conversation: list[dict] = []
        self.worker: ChatWorker | None = None
        self._bubble: MessageBubble | None = None
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Messages
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(SCROLL_STYLE)
        self._msgs = QWidget()
        self._msgs.setStyleSheet("background: transparent;")
        self._msgs_lay = QVBoxLayout(self._msgs)
        self._msgs_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._msgs_lay.setSpacing(4)
        self._msgs_lay.setContentsMargins(8, 8, 8, 8)
        self.scroll.setWidget(self._msgs)
        lay.addWidget(self.scroll, 1)

        # Quick actions
        lay.addWidget(self._build_quick_actions())

        # Input
        inp_w = QWidget()
        inp_w.setFixedHeight(58)
        inp_w.setStyleSheet(f"background: {BG_INPUT}; border-top: 1px solid #1e2a30;")
        il = QHBoxLayout(inp_w)
        il.setContentsMargins(10, 8, 10, 8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Napíš príkaz alebo otázku...")
        self.input.setFont(QFont("Segoe UI", 10))
        self.input.setStyleSheet(f"""
            QLineEdit {{ background: {BG_DARK}; color: #eceff1; border: 1px solid #263238;
                         border-radius: 8px; padding: 6px 12px; }}
            QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
        """)
        self.input.returnPressed.connect(self._send)
        self.send_btn = QPushButton("→")
        self.send_btn.setFixedSize(36, 36)
        self.send_btn.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        self.send_btn.setStyleSheet(BTN_PRIMARY.replace("font-size: 10px;", ""))
        self.send_btn.clicked.connect(self._send)
        il.addWidget(self.input)
        il.addWidget(self.send_btn)
        lay.addWidget(inp_w)

        self._add_bubble("Ahoj! Som AIOS. Čo môžem urobiť?", False)

    def _build_quick_actions(self) -> QWidget:
        BTN_Q = f"""
            QPushButton {{
                background: {BG_CARD}; color: {ACCENT};
                border: 1px solid #1e2a30; border-radius: 6px;
                padding: 4px 6px; font-size: 10px; text-align: left;
            }}
            QPushButton:hover {{ background: #1a2535; border-color: {ACCENT}; }}
        """
        CAT = "QLabel { color: #546e7a; font-size: 9px; font-weight: bold; background: transparent; padding: 2px 4px 0 4px; }"

        categories = [
            ("🖥  Systém", [
                ("CPU / RAM",      "Ukáž využitie CPU, RAM a load average"),
                ("Disk",           "Ukáž obsadenosť disku na všetkých oddieloch"),
                ("Procesy",        "Ukáž top 10 procesov žerúcich najviac CPU"),
                ("Uptime",         "Ako dlho beží systém a kto je prihlásený?"),
            ]),
            ("⚙️  Služby", [
                ("Všetky služby",  "Vypíš všetky bežiace systemd služby"),
                ("Zlyhané",        "Ukáž systemd služby ktoré zlyhali (failed)"),
                ("Nginx",          "Ukáž stav a logy nginx webservera"),
                ("SSH",            "Ukáž stav SSH servera a aktívne pripojenia"),
            ]),
            ("📋  Logy", [
                ("Sys logy",       "Ukáž posledných 50 riadkov system logu (journalctl)"),
                ("Chyby",          "Ukáž len ERROR a CRITICAL záznamy z logov"),
                ("Kernel logy",    "Ukáž kernel správy (dmesg | tail -30)"),
                ("Auth logy",      "Ukáž autentifikačné logy a SSH prístupy"),
            ]),
            ("🐳  Docker", [
                ("Kontajnery",     "Vypíš všetky bežiace Docker kontajnery"),
                ("Stats",          "Ukáž CPU a RAM využitie Docker kontajnerov"),
                ("Volumes",        "Vypíš Docker volumes a ich využitie disku"),
                ("Siete",          "Vypíš Docker siete a ich konfiguráciu"),
            ]),
            ("☸  Kubernetes", [
                ("Pods",           "Vypíš všetky pody v default namespace"),
                ("Nodes",          "Ukáž stav Kubernetes nodov"),
                ("Services",       "Vypíš Kubernetes services a porty"),
                ("Events",         "Ukáž posledné Kubernetes udalosti a chyby"),
            ]),
            ("🔒  Sieť & Security", [
                ("Otvorené porty", "Ukáž všetky lokálne otvorené porty a služby"),
                ("Firewall",       "Ukáž stav UFW firewallu a pravidlá"),
                ("Pripojenia",     "Ukáž aktívne sieťové pripojenia"),
                ("Neúsp. loginy",  "Ukáž posledné neúspešné pokusy o prihlásenie"),
            ]),
            ("🌐  Sieťové nástroje", [
                ("IP adresy",      "Ukáž všetky sieťové rozhrania a IP adresy"),
                ("DNS",            "Ukáž DNS konfiguráciu a otestuj rozlíšenie"),
                ("Ping gateway",   "Otestuj konektivitu na default gateway"),
                ("Bandwidth",      "Ukáž aktuálnu sieťovú rýchlosť"),
            ]),
            ("📁  Git", [
                ("Log",            "Ukáž posledných 10 git commitov"),
                ("Status",         "Ukáž git status aktuálneho repozitára"),
                ("Diff",           "Ukáž git diff necommitnutých zmien"),
                ("Branches",       "Vypíš všetky git vetvy a ich stav"),
            ]),
            ("🗄️  Databázy", [
                ("PostgreSQL",     "Ukáž stav PostgreSQL a zoznam databáz"),
                ("MySQL/MariaDB",  "Ukáž stav MySQL servera a databázy"),
                ("Redis",          "Ukáž info o Redis serveri a využitú pamäť"),
                ("MongoDB",        "Ukáž stav MongoDB a štatistiky"),
            ]),
            ("🐍  Python / Dev", [
                ("Venv aktívne",   "Ukáž aktívne Python virtual environment a verziu"),
                ("Pip balíčky",    "Vypíš nainštalované pip balíčky a verzie"),
                ("Pytest",         "Spusti testy v aktuálnom adresári (pytest -v)"),
                ("Jupyter",        "Ukáž bežiace Jupyter notebook servery"),
            ]),
            ("📦  Balíčky (pacman)", [
                ("Aktualizácie",   "Skontroluj dostupné aktualizácie systému"),
                ("Nainštalované",  "Ukáž posledne nainštalované balíčky"),
                ("Veľkosť",        "Ukáž 10 najväčších nainštalovaných balíčkov"),
                ("Osirotené",      "Nájdi osirotené balíčky ktoré možno odinštalovať"),
            ]),
            ("☁️  Cloud & CI/CD", [
                ("Git remote",     "Ukáž git remote repozitáre a ich URL"),
                ("GitHub Actions", "Ukáž stav posledných GitHub Actions behov"),
                ("Docker images",  "Ukáž lokálne Docker images a ich veľkosť"),
                ("Terraform",      "Ukáž stav Terraform workspace"),
            ]),
            ("💾  Zálohy & Súbory", [
                ("Veľké súbory",   "Nájdi 10 najväčších súborov v /home a /var"),
                ("Disk usage",     "Ukáž využitie disku po adresároch"),
                ("Tmp súbory",     "Nájdi staré dočasné súbory v /tmp"),
                ("Rsync status",   "Ukáž posledné rsync zálohovacie logy"),
            ]),
            ("🤖  AI & Modely", [
                ("Ollama modely",  "Vypíš lokálne nainštalované Ollama modely"),
                ("GPU info",       "Ukáž využitie GPU, VRAM a teplotu"),
                ("AIOS status",    "Ukáž stav AIOS daemona a AI routera"),
                ("Agenti",         "Aké AIOS agenty sú dostupné?"),
            ]),
        ]

        container = QWidget()
        container.setStyleSheet(f"background: {BG_INPUT}; border-top: 1px solid #1e2a30;")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setFixedHeight(175)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(SCROLL_STYLE)

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        vl = QVBoxLayout(inner)
        vl.setContentsMargins(0, 0, 4, 0)
        vl.setSpacing(5)

        for cat_label, buttons in categories:
            cat = QLabel(cat_label)
            cat.setStyleSheet(CAT)
            cat.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            vl.addWidget(cat)

            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            grid = QGridLayout(row_w)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(3)
            for i, (label, prompt) in enumerate(buttons):
                b = QPushButton(label)
                b.setStyleSheet(BTN_Q)
                b.setFixedHeight(24)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _, p=prompt: self._send_text(p))
                grid.addWidget(b, i // 2, i % 2)
            vl.addWidget(row_w)

        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return container

    def _add_bubble(self, text: str, is_user: bool) -> MessageBubble:
        b = MessageBubble(text, is_user)
        self._msgs_lay.addWidget(b)
        QTimer.singleShot(50, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))
        return b

    def _send_text(self, text: str):
        self.input.setText(text)
        self._send()

    def _send(self):
        text = self.input.text().strip()
        if not text or (self.worker and self.worker.isRunning()):
            return
        self.input.clear()
        self._add_bubble(text, True)
        self.conversation.append({"role": "user", "content": text})
        self.send_btn.setEnabled(False)
        self.input.setEnabled(False)
        self._bubble = self._add_bubble("...", False)
        self.worker = ChatWorker(list(self.conversation))
        self.worker.chunk_received.connect(self._on_chunk)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(self._on_done)
        self.worker.start()

    @pyqtSlot(str)
    def _on_chunk(self, text: str):
        if self._bubble:
            self._bubble.update_text(text)
            self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())

    @pyqtSlot()
    def _on_done(self):
        if self._bubble:
            self.conversation.append({"role": "assistant", "content": self._bubble._lbl.text()})
        self.send_btn.setEnabled(True)
        self.input.setEnabled(True)
        self.input.setFocus()

    @pyqtSlot(str)
    def _on_error(self, err: str):
        if self._bubble:
            self._bubble.update_text(err)
        self.send_btn.setEnabled(True)
        self.input.setEnabled(True)


# ─── Agent create dialog ──────────────────────────────────────────────────────

class AgentCreateDialog(QDialog):
    def __init__(self, parent=None, existing: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Nový agent" if not existing else "Upraviť agenta")
        self.setMinimumWidth(400)
        self.setStyleSheet(f"""
            QDialog {{ background: {BG_DARK}; color: #eceff1; }}
            QLabel {{ color: #eceff1; background: transparent; }}
            QLineEdit, QPlainTextEdit {{
                background: {BG_CARD}; color: #eceff1;
                border: 1px solid #263238; border-radius: 6px; padding: 6px;
            }}
            QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
            QGroupBox {{ color: #546e7a; border: 1px solid #263238; border-radius: 6px;
                         margin-top: 8px; padding-top: 8px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {ACCENT}; }}
            QCheckBox {{ color: #90a4ae; background: transparent; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid #263238;
                                    border-radius: 3px; background: {BG_CARD}; }}
            QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
        """)

        lay = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(8)

        self.name_edit = QLineEdit(existing.get("name", "") if existing else "")
        self.name_edit.setPlaceholderText("napr. Môj DevOps agent")
        form.addRow("Názov:", self.name_edit)

        self.desc_edit = QLineEdit(existing.get("description", "") if existing else "")
        self.desc_edit.setPlaceholderText("Krátky popis čo agent robí")
        form.addRow("Popis:", self.desc_edit)

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(
            "Systémový prompt — definuje správanie agenta.\n\n"
            "napr.: Si expert na databázy. Pomáhaš s PostgreSQL, MySQL a Redis.\n"
            "Vždy najprv diagnostikuješ problém, potom navrhuješ riešenie."
        )
        self.prompt_edit.setPlainText(existing.get("system_prompt", "") if existing else "")
        self.prompt_edit.setFixedHeight(120)
        form.addRow("Systémový\nprompt:", self.prompt_edit)

        lay.addLayout(form)

        # Tools
        tools_box = QGroupBox("Nástroje agenta")
        tools_grid = QGridLayout(tools_box)
        tools_grid.setSpacing(4)
        selected_tools = set(existing.get("tools", AVAILABLE_TOOLS) if existing else AVAILABLE_TOOLS)
        self._tool_checks: dict[str, QCheckBox] = {}
        for i, tool in enumerate(AVAILABLE_TOOLS):
            cb = QCheckBox(tool)
            cb.setChecked(tool in selected_tools)
            cb.setFont(QFont("JetBrains Mono", 9))
            self._tool_checks[tool] = cb
            tools_grid.addWidget(cb, i // 2, i % 2)
        lay.addWidget(tools_box)

        # Buttons
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.setStyleSheet(f"""
            QPushButton {{ background: {BG_CARD}; color: {ACCENT}; border: 1px solid #263238;
                           border-radius: 6px; padding: 5px 16px; }}
            QPushButton:hover {{ background: #1a2535; }}
            QPushButton[text="Save"] {{ background: {ACCENT}; color: #000; border: none; }}
        """)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def get_data(self) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "name": self.name_edit.text().strip() or "Bez názvu",
            "description": self.desc_edit.text().strip(),
            "system_prompt": self.prompt_edit.toPlainText().strip(),
            "tools": [t for t, cb in self._tool_checks.items() if cb.isChecked()],
            "type": "custom",
            "endpoint": "/agent/run",
            "created_at": datetime.now().isoformat(),
        }


# ─── Agent card ───────────────────────────────────────────────────────────────

class AgentCard(QWidget):
    run_requested = pyqtSignal(dict)
    edit_requested = pyqtSignal(dict)
    delete_requested = pyqtSignal(str)

    def __init__(self, agent: dict):
        super().__init__()
        self.agent = agent
        is_builtin = agent.get("type") == "builtin"

        self.setStyleSheet(f"""
            QWidget#card {{
                background: {BG_CARD}; border: 1px solid #1e2a30;
                border-radius: 8px;
            }}
            QWidget#card:hover {{ border-color: {ACCENT}; }}
        """)
        self.setObjectName("card")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        # Header row
        hdr = QHBoxLayout()
        name = QLabel(agent["name"])
        name.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        name.setStyleSheet(f"color: {ACCENT}; background: transparent;")

        badge = QLabel("zabudovaný" if is_builtin else "vlastný")
        badge.setFont(QFont("Segoe UI", 8))
        badge.setStyleSheet(f"""
            background: {'#0d2a2e' if is_builtin else '#1a1a3e'};
            color: {'#4dd0e1' if is_builtin else '#9575cd'};
            border-radius: 4px; padding: 1px 6px;
        """)
        hdr.addWidget(name)
        hdr.addStretch()
        hdr.addWidget(badge)
        lay.addLayout(hdr)

        # Description
        if agent.get("description"):
            desc = QLabel(agent["description"])
            desc.setWordWrap(True)
            desc.setFont(QFont("Segoe UI", 9))
            desc.setStyleSheet("color: #78909c; background: transparent;")
            lay.addWidget(desc)

        # Task input + run
        task_row = QHBoxLayout()
        self.task_input = QLineEdit()
        self.task_input.setPlaceholderText("Zadaj úlohu pre agenta...")
        self.task_input.setFont(QFont("Segoe UI", 9))
        self.task_input.setFixedHeight(28)
        self.task_input.setStyleSheet(f"""
            QLineEdit {{ background: {BG_DARK}; color: #eceff1;
                         border: 1px solid #263238; border-radius: 5px; padding: 4px 8px; }}
            QLineEdit:focus {{ border-color: {ACCENT}; }}
        """)
        self.task_input.returnPressed.connect(self._run)

        run_btn = QPushButton("▶ Spusti")
        run_btn.setFixedHeight(28)
        run_btn.setStyleSheet(BTN_PRIMARY)
        run_btn.clicked.connect(self._run)
        task_row.addWidget(self.task_input)
        task_row.addWidget(run_btn)
        lay.addLayout(task_row)

        # Edit/Delete (only custom)
        if not is_builtin:
            ctrl = QHBoxLayout()
            ctrl.addStretch()
            edit_btn = QPushButton("Upraviť")
            edit_btn.setFixedHeight(22)
            edit_btn.setStyleSheet(BTN)
            edit_btn.clicked.connect(lambda: self.edit_requested.emit(self.agent))
            del_btn = QPushButton("Zmazať")
            del_btn.setFixedHeight(22)
            del_btn.setStyleSheet(BTN_DANGER)
            del_btn.clicked.connect(lambda: self.delete_requested.emit(self.agent["id"]))
            ctrl.addWidget(edit_btn)
            ctrl.addWidget(del_btn)
            lay.addLayout(ctrl)

    def _run(self):
        task = self.task_input.text().strip()
        if task:
            self.run_requested.emit({**self.agent, "_task": task})
            self.task_input.clear()


# ─── Agent run output dialog ──────────────────────────────────────────────────

class AgentRunWindow(QDialog):
    def __init__(self, agent: dict, parent=None):
        super().__init__(parent)
        self.agent = agent
        self.setWindowTitle(f"Agent: {agent['name']}")
        self.setMinimumSize(480, 500)
        self.setStyleSheet(f"""
            QDialog {{ background: {BG_DARK}; color: #eceff1; }}
            QLabel {{ background: transparent; color: #eceff1; }}
            QPlainTextEdit {{
                background: #0a0f14; color: #a5d6a7;
                border: 1px solid #1e2a30; border-radius: 6px;
                font-family: 'JetBrains Mono', monospace; font-size: 10px;
            }}
        """)

        lay = QVBoxLayout(self)

        # Info header
        hdr = QHBoxLayout()
        self.status_lbl = QLabel("⏳ Spúšťam...")
        self.status_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.status_lbl.setStyleSheet(f"color: {ACCENT};")
        self.time_lbl = QLabel("")
        self.time_lbl.setStyleSheet("color: #546e7a;")
        hdr.addWidget(self.status_lbl)
        hdr.addStretch()
        hdr.addWidget(self.time_lbl)
        lay.addLayout(hdr)

        task_lbl = QLabel(f"Úloha: {agent['_task']}")
        task_lbl.setWordWrap(True)
        task_lbl.setStyleSheet("color: #78909c; font-size: 10px;")
        lay.addWidget(task_lbl)

        # Output log
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        lay.addWidget(self.output, 1)

        # Steps / cost info
        self.meta_lbl = QLabel("")
        self.meta_lbl.setStyleSheet("color: #546e7a; font-size: 9px;")
        lay.addWidget(self.meta_lbl)

        # Close
        self.close_btn = QPushButton("Zatvoriť")
        self.close_btn.setStyleSheet(BTN)
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        lay.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignRight)

        # Timer
        self._start = datetime.now()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        # Worker
        self.worker = AgentWorker(agent["_task"], agent.get("endpoint", "/agent/run"))
        self.worker.output.connect(self._append)
        self.worker.finished.connect(self._done)
        self.worker.error.connect(self._err)
        self.worker.start()

    def _tick(self):
        elapsed = (datetime.now() - self._start).seconds
        self.time_lbl.setText(f"{elapsed}s")

    def _append(self, text: str):
        self.output.appendPlainText(text)

    @pyqtSlot(dict)
    def _done(self, data: dict):
        self._timer.stop()
        status = data.get("status", "?")
        icon = "✅" if status == "completed" else "❌"
        self.status_lbl.setText(f"{icon} {status.upper()}")
        self.status_lbl.setStyleSheet(
            f"color: {'#66bb6a' if status == 'completed' else '#ef5350'}; font-weight: bold;"
        )
        steps = data.get("steps", 0)
        duration = data.get("duration_s", 0)
        self.meta_lbl.setText(f"Kroky: {steps}  |  Čas: {duration:.1f}s")
        if data.get("output"):
            self.output.appendPlainText(f"\n{'─'*40}\n{data['output']}")
        if data.get("error"):
            self.output.appendPlainText(f"\n⚠️ {data['error']}")
        self.close_btn.setEnabled(True)

    @pyqtSlot(str)
    def _err(self, err: str):
        self._timer.stop()
        self.status_lbl.setText("❌ CHYBA")
        self.status_lbl.setStyleSheet("color: #ef5350; font-weight: bold;")
        self.output.appendPlainText(f"\nChyba: {err}")
        self.close_btn.setEnabled(True)


# ─── Agents tab ───────────────────────────────────────────────────────────────

class AgentsTab(QWidget):
    def __init__(self, parent_window):
        super().__init__()
        self.parent_window = parent_window
        self._custom_agents: list[dict] = self._load_agents()
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        # Toolbar
        tb = QHBoxLayout()
        title = QLabel("Správa agentov")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {ACCENT};")
        new_btn = QPushButton("+ Nový agent")
        new_btn.setStyleSheet(BTN_PRIMARY)
        new_btn.clicked.connect(self._create_agent)
        tb.addWidget(title)
        tb.addStretch()
        tb.addWidget(new_btn)
        lay.addLayout(tb)

        # Scrollable agent list
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(SCROLL_STYLE)
        self._list_w = QWidget()
        self._list_w.setStyleSheet("background: transparent;")
        self._list_lay = QVBoxLayout(self._list_w)
        self._list_lay.setContentsMargins(0, 0, 4, 0)
        self._list_lay.setSpacing(6)
        self._list_lay.addStretch()
        self.scroll.setWidget(self._list_w)
        lay.addWidget(self.scroll, 1)

        self._refresh_list()

    def _refresh_list(self):
        # Remove all existing cards (not the stretch)
        while self._list_lay.count() > 1:
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Built-in agents first
        builtin_lbl = QLabel("Zabudované agenty")
        builtin_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        builtin_lbl.setStyleSheet("color: #546e7a; background: transparent;")
        self._list_lay.insertWidget(0, builtin_lbl)

        for i, agent in enumerate(BUILTIN_AGENTS):
            card = AgentCard(agent)
            card.run_requested.connect(self._run_agent)
            self._list_lay.insertWidget(i + 1, card)

        # Custom agents
        if self._custom_agents:
            custom_lbl = QLabel("Vlastné agenty")
            custom_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            custom_lbl.setStyleSheet("color: #546e7a; background: transparent; margin-top: 4px;")
            self._list_lay.insertWidget(len(BUILTIN_AGENTS) + 1, custom_lbl)

            for j, agent in enumerate(self._custom_agents):
                card = AgentCard(agent)
                card.run_requested.connect(self._run_agent)
                card.edit_requested.connect(self._edit_agent)
                card.delete_requested.connect(self._delete_agent)
                self._list_lay.insertWidget(len(BUILTIN_AGENTS) + 2 + j, card)

    def _run_agent(self, agent: dict):
        dlg = AgentRunWindow(agent, self.parent_window)
        dlg.exec()

    def _create_agent(self):
        dlg = AgentCreateDialog(self.parent_window)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            self._custom_agents.append(data)
            self._save_agents()
            self._refresh_list()

    def _edit_agent(self, agent: dict):
        dlg = AgentCreateDialog(self.parent_window, existing=agent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updated = dlg.get_data()
            updated["id"] = agent["id"]
            updated["created_at"] = agent.get("created_at", updated["created_at"])
            self._custom_agents = [
                updated if a["id"] == agent["id"] else a
                for a in self._custom_agents
            ]
            self._save_agents()
            self._refresh_list()

    @pyqtSlot(str)
    def _delete_agent(self, agent_id: str):
        self._custom_agents = [a for a in self._custom_agents if a["id"] != agent_id]
        self._save_agents()
        self._refresh_list()

    def _load_agents(self) -> list[dict]:
        try:
            if AGENTS_FILE.exists():
                return json.loads(AGENTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return []

    def _save_agents(self):
        AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        AGENTS_FILE.write_text(
            json.dumps(self._custom_agents, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ─── Main window ──────────────────────────────────────────────────────────────

class AIOSPanel(QMainWindow):
    def __init__(self):
        super().__init__()
        self._setup_window()
        self._setup_ui()
        self._setup_tray()
        self._setup_timer()

    def _setup_window(self):
        self.setWindowTitle("AIOS Panel")
        self.setFixedWidth(PANEL_WIDTH)
        if platform.system() == "Linux":
            self.setWindowFlags(
                Qt.WindowType.Tool |
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        else:
            self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)

        s = QApplication.primaryScreen().geometry()
        self.setGeometry(s.width() - PANEL_WIDTH - 8, 40, PANEL_WIDTH, s.height() - 80)

    def _setup_ui(self):
        radius = "12px" if platform.system() == "Linux" else "0px"
        central = QWidget()
        central.setObjectName("central")
        central.setStyleSheet(f"""
            #central {{
                background: {BG_DARK};
                border-radius: {radius};
                border: 1px solid #263238;
            }}
        """)
        self.setCentralWidget(central)
        main = QVBoxLayout(central)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(46)
        hdr.setStyleSheet(f"""
            background: #0d1117;
            border-radius: {radius} {radius} 0 0;
            border-bottom: 2px solid {ACCENT};
        """)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 0, 12, 0)

        title = QLabel("⚡ AIOS")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {ACCENT}; background: transparent;")

        self.status_dot = QLabel("●")
        self.status_dot.setFont(QFont("Segoe UI", 14))
        self.status_dot.setStyleSheet("color: #546e7a; background: transparent;")
        self.status_dot.setToolTip("Daemon offline")

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 26)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #546e7a; border: none; font-size: 13px; }"
            "QPushButton:hover { color: #ef5350; }"
        )
        close_btn.clicked.connect(self.hide)
        hl.addWidget(title)
        hl.addStretch()
        hl.addWidget(self.status_dot)
        hl.addWidget(close_btn)
        main.addWidget(hdr)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: none; background: {BG_DARK}; }}
            QTabBar::tab {{
                background: #0d1117; color: #546e7a;
                padding: 6px 16px; border: none;
                border-bottom: 2px solid transparent;
                font-size: 10px; font-weight: bold;
            }}
            QTabBar::tab:selected {{
                color: {ACCENT};
                border-bottom: 2px solid {ACCENT};
                background: {BG_DARK};
            }}
            QTabBar::tab:hover {{ color: #80deea; background: #0d1117; }}
        """)

        self.chat_tab = ChatTab()
        self.agents_tab = AgentsTab(self)

        self.tabs.addTab(self.chat_tab, "💬  Chat")
        self.tabs.addTab(self.agents_tab, "🤖  Agenti")
        main.addWidget(self.tabs, 1)

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        px = QPixmap(22, 22)
        px.fill(QColor(ACCENT))
        p = QPainter(px)
        p.setPen(QColor("#000"))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "AI")
        p.end()
        self.tray.setIcon(QIcon(px))
        self.tray.setToolTip("AIOS Panel")
        menu = QMenu()
        menu.addAction("Otvoriť", self.toggle_visibility)
        menu.addSeparator()
        menu.addAction("Zatvoriť", QApplication.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.toggle_visibility()
            if r == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self.tray.show()

    def _setup_timer(self):
        t = QTimer(self)
        t.timeout.connect(self._check_daemon)
        t.start(8000)
        self._check_daemon()

    def _check_daemon(self):
        try:
            r = httpx.get(f"{DAEMON_URL}/health", timeout=1.5)
            ok = r.status_code == 200
        except Exception:
            ok = False
        self.status_dot.setStyleSheet(
            f"color: {'#66bb6a' if ok else '#ef5350'}; background: transparent;"
        )
        self.status_dot.setToolTip("Daemon online" if ok else "Daemon offline — spusti aiosd")
        self.tray.setToolTip("AIOS — online" if ok else "AIOS — offline")

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("AIOS Panel")
    app.setQuitOnLastWindowClosed(False)

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG_DARK))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#eceff1"))
    pal.setColor(QPalette.ColorRole.Base, QColor("#0d1117"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#eceff1"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    app.setPalette(pal)

    panel = AIOSPanel()
    panel.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
