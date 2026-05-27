#!/usr/bin/env python3
"""
AIOS Panel — floating AI assistant window for KDE Plasma.
Sits on the right side of the screen, toggled with Super+A.
Uses PyQt6 for native look on KDE.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import httpx
from PyQt6.QtCore import (
    QSize, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
)
from PyQt6.QtGui import QColor, QFont, QIcon, QPalette
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QPushButton, QScrollArea, QSizePolicy,
    QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget, QMenu
)

DAEMON_URL = "http://127.0.0.1:7474"
PANEL_WIDTH = 420
ACCENT = "#00bcd4"
BG_DARK = "#1a1a2e"
BG_MSG = "#16213e"
BG_USER = "#0f3460"


class MessageWorker(QThread):
    chunk_received = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, messages: list[dict]):
        super().__init__()
        self.messages = messages

    def run(self):
        try:
            with httpx.Client(timeout=120.0) as client:
                with client.stream("POST", f"{DAEMON_URL}/chat", json={
                    "messages": self.messages,
                    "stream": False,
                }) as resp:
                    resp.raise_for_status()
                    data = resp.json()
                    self.chunk_received.emit(data.get("response", ""))
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()


class MessageBubble(QWidget):
    def __init__(self, text: str, is_user: bool, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setFont(QFont("JetBrains Mono", 10) if not is_user
                      else QFont("Segoe UI", 10))

        role_label = QLabel("Ty" if is_user else "AIOS")
        role_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        role_label.setStyleSheet(f"color: {ACCENT if not is_user else '#90a4ae'};")

        bg = BG_USER if is_user else BG_MSG
        label.setStyleSheet(f"""
            background-color: {bg};
            color: #eceff1;
            border-radius: 8px;
            padding: 8px 12px;
        """)

        layout.addWidget(role_label)
        layout.addWidget(label)
        self.setStyleSheet("background: transparent;")

    def update_text(self, text: str):
        for i in range(self.layout().count()):
            w = self.layout().itemAt(i).widget()
            if isinstance(w, QLabel) and w.text() not in ("Ty", "AIOS"):
                w.setText(text)
                break


class AIOSPanel(QMainWindow):
    def __init__(self):
        super().__init__()
        self.conversation: list[dict] = []
        self.worker: MessageWorker | None = None
        self._current_bubble: MessageBubble | None = None

        self._setup_window()
        self._setup_ui()
        self._setup_tray()
        self._setup_timer()

    def _setup_window(self):
        self.setWindowTitle("AIOS Panel")
        self.setFixedWidth(PANEL_WIDTH)
        self.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(
            screen.width() - PANEL_WIDTH - 8,
            40,
            PANEL_WIDTH,
            screen.height() - 80,
        )

    def _setup_ui(self):
        central = QWidget()
        central.setObjectName("central")
        central.setStyleSheet(f"""
            #central {{
                background-color: {BG_DARK};
                border-radius: 12px;
                border: 1px solid #263238;
            }}
        """)
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet(f"""
            background-color: #0d1117;
            border-radius: 12px 12px 0 0;
            border-bottom: 1px solid {ACCENT};
        """)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 12, 0)

        title = QLabel("⚡ AIOS Assistant")
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {ACCENT}; background: transparent;")

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #546e7a; background: transparent;")
        self.status_dot.setFont(QFont("Segoe UI", 14))

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #546e7a; border: none; font-size: 14px; }
            QPushButton:hover { color: #f44336; }
        """)
        close_btn.clicked.connect(self.hide)

        h_layout.addWidget(title)
        h_layout.addStretch()
        h_layout.addWidget(self.status_dot)
        h_layout.addWidget(close_btn)
        main_layout.addWidget(header)

        # Messages area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { background: #1a1a2e; width: 6px; border-radius: 3px; }
            QScrollBar::handle:vertical { background: #263238; border-radius: 3px; }
        """)

        self.messages_widget = QWidget()
        self.messages_widget.setStyleSheet("background: transparent;")
        self.messages_layout = QVBoxLayout(self.messages_widget)
        self.messages_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.messages_layout.setSpacing(4)
        self.messages_layout.setContentsMargins(8, 8, 8, 8)
        self.scroll.setWidget(self.messages_widget)
        main_layout.addWidget(self.scroll, 1)

        # Quick actions — kategorizované tlačidlá
        main_layout.addWidget(self._build_quick_actions())

        # Input area
        input_widget = QWidget()
        input_widget.setFixedHeight(60)
        input_widget.setStyleSheet(f"""
            background: #0d1117;
            border-radius: 0 0 12px 12px;
            border-top: 1px solid #263238;
        """)
        i_layout = QHBoxLayout(input_widget)
        i_layout.setContentsMargins(10, 8, 10, 8)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Napíš príkaz alebo otázku...")
        self.input.setFont(QFont("Segoe UI", 10))
        self.input.setStyleSheet(f"""
            QLineEdit {{
                background: #1a1a2e;
                color: #eceff1;
                border: 1px solid #263238;
                border-radius: 8px;
                padding: 6px 12px;
            }}
            QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
        """)
        self.input.returnPressed.connect(self._on_send)

        send_btn = QPushButton("→")
        send_btn.setFixedSize(36, 36)
        send_btn.setFont(QFont("Segoe UI", 16))
        send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ACCENT};
                color: #000;
                border-radius: 8px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background: #00e5ff; }}
            QPushButton:disabled {{ background: #263238; color: #546e7a; }}
        """)
        send_btn.clicked.connect(self._on_send)
        self.send_btn = send_btn

        i_layout.addWidget(self.input)
        i_layout.addWidget(send_btn)
        main_layout.addWidget(input_widget)

        # Welcome message
        self._add_bubble("Ahoj! Som AIOS. Čo môžem urobiť?", is_user=False)

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        # Use a simple colored icon
        from PyQt6.QtGui import QPixmap, QPainter
        px = QPixmap(22, 22)
        px.fill(QColor(ACCENT))
        painter = QPainter(px)
        painter.setPen(QColor("#000"))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "AI")
        painter.end()
        self.tray.setIcon(QIcon(px))
        self.tray.setToolTip("AIOS Assistant")

        menu = QMenu()
        menu.addAction("Otvoriť AIOS", self.toggle_visibility)
        menu.addSeparator()
        menu.addAction("Zatvoriť", QApplication.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_clicked)
        self.tray.show()

    def _setup_timer(self):
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._check_daemon)
        self.status_timer.start(10000)
        self._check_daemon()

    def _check_daemon(self):
        try:
            r = httpx.get(f"{DAEMON_URL}/health", timeout=1.5)
            ok = r.status_code == 200
        except Exception:
            ok = False
        color = ACCENT if ok else "#f44336"
        self.status_dot.setStyleSheet(f"color: {color}; background: transparent;")
        self.tray.setToolTip("AIOS — online" if ok else "AIOS — daemon offline")

    def _tray_clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visibility()

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()
            self.input.setFocus()

    def _add_bubble(self, text: str, is_user: bool) -> MessageBubble:
        bubble = MessageBubble(text, is_user)
        self.messages_layout.addWidget(bubble)
        QTimer.singleShot(50, self._scroll_to_bottom)
        return bubble

    def _scroll_to_bottom(self):
        self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()
        )

    def _build_quick_actions(self) -> QWidget:
        """
        Kategorizované rýchle tlačidlá v mriežke.
        Každá kategória má farebnú hlavičku a 2–4 tlačidlá.
        """
        BTN_STYLE = f"""
            QPushButton {{
                background: #0d1117;
                color: {ACCENT};
                border: 1px solid #1e2a30;
                border-radius: 6px;
                padding: 4px 6px;
                font-size: 10px;
                text-align: left;
            }}
            QPushButton:hover {{ background: #1a2535; border-color: {ACCENT}; }}
            QPushButton:pressed {{ background: #263238; }}
        """
        CAT_STYLE = """
            QLabel {
                color: #546e7a;
                font-size: 9px;
                font-weight: bold;
                background: transparent;
                padding: 2px 4px 0 4px;
            }
        """

        # Definícia kategórií: (ikona + názov, [(label, prompt)])
        categories = [
            ("🖥  Systém", [
                ("CPU / RAM",      "Ukáž využitie CPU, RAM a load average"),
                ("Disk",           "Ukáž obsadenosť disku na všetkých oddieloch"),
                ("Procesy",        "Ukáž top 10 procesov žerúcich najviac CPU"),
                ("Uptime",         "Ako dlho beží systém a kto je prihlásený?"),
            ]),
            ("🐳  Docker", [
                ("Kontajnery",     "Vypíš všetky bežiace Docker kontajnery"),
                ("Stats",          "Ukáž CPU a RAM využitie Docker kontajnerov"),
                ("Logy (nginx)",   "Ukáž posledných 50 riadkov logov kontajnera nginx"),
                ("Siete",          "Vypíš Docker siete a ich konfigurácию"),
            ]),
            ("☸  Kubernetes", [
                ("Pods",           "Vypíš všetky pody v default namespace"),
                ("Nodes",          "Ukáž stav Kubernetes nodov"),
                ("Services",       "Vypíš Kubernetes services"),
                ("Events",         "Ukáž posledné Kubernetes udalosti a chyby"),
            ]),
            ("🔒  Sieť & Security", [
                ("Otvorené porty", "Ukáž všetky lokálne otvorené porty a služby"),
                ("Firewall",       "Ukáž stav UFW firewallu a pravidlá"),
                ("Pripojenia",     "Ukáž aktívne sieťové pripojenia"),
                ("Neúsp. loginy",  "Ukáž posledné neúspešné pokusy o prihlásenie"),
            ]),
            ("📁  Git", [
                ("Log",            "Ukáž posledných 10 git commitov"),
                ("Status",         "Ukáž git status aktuálneho repozitára"),
                ("Diff",           "Ukáž git diff necommitnutých zmien"),
                ("Branches",       "Vypíš všetky git vetvy"),
            ]),
            ("🤖  AI & Modely", [
                ("Ollama modely",  "Vypíš lokálne nainštalované Ollama modely"),
                ("GPU info",       "Ukáž využitie GPU a VRAM"),
                ("AIOS status",    "Ukáž stav AIOS daemona, AI router a hlas"),
                ("Agenti",         "Aké AIOS agenty sú dostupné a čo vedia?"),
            ]),
        ]

        container = QWidget()
        container.setStyleSheet("background: #0a0f14; border-top: 1px solid #1e2a30;")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(2)

        # Scrollovateľná oblasť pre tlačidlá
        scroll = QScrollArea()
        scroll.setFixedHeight(180)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { background: #0a0f14; width: 5px; border-radius: 2px; }
            QScrollBar::handle:vertical { background: #1e2a30; border-radius: 2px; }
        """)

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        grid_layout = QVBoxLayout(inner)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(6)

        for cat_label, buttons in categories:
            # Kategória hlavička
            cat_header = QLabel(cat_label)
            cat_header.setStyleSheet(CAT_STYLE)
            cat_header.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            grid_layout.addWidget(cat_header)

            # Mriežka 2 stĺpce
            row_widget = QWidget()
            row_widget.setStyleSheet("background: transparent;")
            row = QGridLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(3)

            for i, (label, prompt) in enumerate(buttons):
                btn = QPushButton(label)
                btn.setStyleSheet(BTN_STYLE)
                btn.setFixedHeight(26)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda _, p=prompt: self._send(p))
                row.addWidget(btn, i // 2, i % 2)

            grid_layout.addWidget(row_widget)

        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return container

    def _on_send(self):
        text = self.input.text().strip()
        if not text or (self.worker and self.worker.isRunning()):
            return
        self._send(text)

    def _send(self, text: str):
        self.input.clear()
        self._add_bubble(text, is_user=True)
        self.conversation.append({"role": "user", "content": text})
        self.send_btn.setEnabled(False)
        self.input.setEnabled(False)

        self._current_bubble = self._add_bubble("...", is_user=False)

        self.worker = MessageWorker(self.conversation.copy())
        self.worker.chunk_received.connect(self._on_chunk)
        self.worker.finished.connect(self._on_done)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    @pyqtSlot(str)
    def _on_chunk(self, text: str):
        if self._current_bubble:
            self._current_bubble.update_text(text)
            self._scroll_to_bottom()

    @pyqtSlot()
    def _on_done(self):
        if self._current_bubble:
            for i in range(self._current_bubble.layout().count()):
                w = self._current_bubble.layout().itemAt(i).widget()
                if isinstance(w, QLabel) and w.text() not in ("Ty", "AIOS"):
                    self.conversation.append({"role": "assistant", "content": w.text()})
                    break
        self.send_btn.setEnabled(True)
        self.input.setEnabled(True)
        self.input.setFocus()

    @pyqtSlot(str)
    def _on_error(self, error: str):
        if self._current_bubble:
            self._current_bubble.update_text(f"Chyba: {error}")
        self.send_btn.setEnabled(True)
        self.input.setEnabled(True)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("AIOS Panel")
    app.setQuitOnLastWindowClosed(False)

    # Dark palette
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG_DARK))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#eceff1"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#0d1117"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#eceff1"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    app.setPalette(palette)

    panel = AIOSPanel()
    panel.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
