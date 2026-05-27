# AIOS — AI Operating System

> AI-native Linux operating system for IT professionals.  
> Built on Arch Linux + Sway (Wayland). Local-first AI. Voice-controlled. IT-ready out of the box.

---

## Features

- **AI Router** — automatically routes between local Ollama and Claude API based on task complexity
- **Voice Control** — wake word `aios` → speak your command → system responds via TTS
- **IT Agent** — shell, Docker, Kubernetes, Git, network tools — all callable by AI
- **Specialized Agents** — DevOps (Terraform, Helm, Ansible), ML Dev (Jupyter, Ollama), Security (Lynis, UFW, log analysis)
- **Wayland Desktop** — Sway WM + Waybar with live AI status panel
- **REST + WebSocket API** — daemon on port 7474, fully scriptable

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    AIOS Desktop (Sway)                   │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  AI Terminal │  │  Waybar + AI │  │  Voice Module │  │
│  │  (aios shell)│  │  status panel│  │  (wake: aios) │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬────────┘  │
└─────────┼────────────────┼─────────────────┼────────────┘
          │                │                 │
          ▼                ▼                 ▼
┌─────────────────────────────────────────────────────────┐
│                  aiosd (FastAPI daemon)                  │
│            REST API + WebSocket  :7474                   │
├─────────────────────────────────────────────────────────┤
│                      AI Router                           │
│   local_first: Ollama ──► Claude API (fallback/complex)  │
├──────────────┬──────────────────┬────────────────────────┤
│   IT Agent   │  DevOps Agent    │  ML Agent  │ Sec Agent │
│  shell/K8s   │  Terraform/Helm  │  Jupyter   │  Lynis   │
└──────────────┴──────────────────┴────────────┴───────────┘
```

---

## Stack

| Vrstva | Technológia |
|---|---|
| Base OS | Arch Linux (rolling release) |
| Desktop | Sway (Wayland) + Waybar + Alacritty |
| AI — lokálne | Ollama · llama3.2:3b · qwen2.5-coder:7b |
| AI — cloud | Claude API (Anthropic) |
| STT | faster-whisper (Whisper medium, int8) |
| TTS | Piper TTS (sk_SK-lili + en_US-ryan) |
| Wake word | openWakeWord (fallback: keyword match) |
| Daemon | FastAPI + uvicorn + asyncio |
| Jazyk | Python 3.12 + Shell |

---

## Štruktúra projektu

```
aios/
├── core/
│   ├── routing/        # AI router (local ↔ cloud)
│   ├── daemon/         # aiosd — hlavný FastAPI daemon
│   ├── agents/         # Agent framework + IT agent
│   ├── tools/          # Async systémové nástroje
│   └── voice/          # STT · TTS · voice interface
├── services/
│   ├── devops/         # DevOps agent (Terraform, Helm, Ansible)
│   ├── mldev/          # ML dev agent (Jupyter, Ollama, venv)
│   └── security/       # Security agent (Lynis, UFW, logy)
├── desktop/
│   ├── compositor/     # Sway config
│   └── panel/          # Waybar config + CSS
├── installer/
│   └── install.sh      # Arch Linux inštalátor
├── scripts/
│   ├── aios            # CLI vstupný bod
│   ├── aios-voice-trigger
│   ├── aios-waybar-status
│   └── aios-waybar-model
└── config/
    └── aios.toml       # Hlavný konfiguračný súbor
```

---

## Inštalácia

### Požiadavky
- Čistý Arch Linux (minimálna inštalácia)
- GPU odporúčaná pre lokálne modely (funguje aj na CPU)
- 16 GB RAM+ odporúčané

### 1. Klonovanie repozitára

```bash
git clone https://github.com/deezeed/aios.git /opt/aios
cd /opt/aios
```

### 2. Spustenie inštalátora

```bash
sudo bash installer/install.sh
```

Inštalátor automaticky nainštaluje:
- Wayland desktop (Sway, Waybar, Alacritty)
- Docker + Docker Compose + lazydocker
- kubectl + Helm + k9s
- Ollama + modely (llama3.2:3b, qwen2.5-coder:7b)
- Python dependencies + faster-whisper + Piper TTS
- systemd service `aiosd`
- SDDM display manager s AIOS session

### 3. Konfigurácia API kľúča

```bash
sudo nano /etc/aios/environment
# Nastav:
# ANTHROPIC_API_KEY=sk-ant-...
```

### 4. Reboot a prihlásenie

```bash
reboot
# Vyber "AIOS (Sway)" session v SDDM
```

---

## Použitie

### CLI shell

```bash
aios shell          # interaktívny AI shell
aios chat "..."     # jednorázový dotaz
aios agent "..."    # spusti IT agenta
aios docker         # Docker status
aios sysinfo        # CPU / RAM / disk / network
```

### Klávesové skratky (Sway)

| Skratka | Akcia |
|---|---|
| `Super + A` | AI panel toggle |
| `Super + V` | Hlasový príkaz |
| `Super + Shift + A` | AIOS shell v termináli |
| `Super + Shift + D` | lazydocker |
| `Super + Shift + K` | k9s (Kubernetes TUI) |
| `Super + Enter` | Terminál |

### Hlasové ovládanie

Povedz **`aios`** (wake word) → systém odpovie „Počúvam." → povedz príkaz.

Príklady:
- *„aios, aké kontajnery bežia?"*
- *„aios, skontroluj voľné miesto na disku"*
- *„aios, reštartuj nginx"*

### REST API

```bash
# Chat
curl -X POST http://localhost:7474/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "aké procesy žerú CPU?"}]}'

# Spusti agenta
curl -X POST http://localhost:7474/agent/run \
  -d '{"task": "skontroluj logy nginx za posledných 100 riadkov"}'

# Docker status
curl http://localhost:7474/docker/ps

# Systémové info
curl http://localhost:7474/system/info

# TTS
curl -X POST http://localhost:7474/voice/speak \
  -d '{"text": "Server je v poriadku."}'
```

---

## AI konfigurácia

Súbor `config/aios.toml`:

```toml
[ai.router]
strategy = "local_first"   # local_first | cloud_first | cost_optimized | speed_optimized
fallback_to_cloud = true

[ai.local]
default_model = "llama3.2:3b"
code_model = "qwen2.5-coder:7b"

[ai.cloud]
model = "claude-sonnet-4-6"
```

Router automaticky:
- Jednoduché otázky → lokálny rýchly model
- Kód → `qwen2.5-coder`
- Komplexná analýza / bezpečnosť → Claude API
- Lokálny model nedostupný → fallback na cloud

---

## Vlastný agent

```python
from core.agents.base import BaseAgent, ToolDefinition
from core.routing.router import AIRouter

class MojAgent(BaseAgent):
    @property
    def system_prompt(self) -> str:
        return "Si expert na ..."

    def setup_tools(self):
        self.register_tool(ToolDefinition(
            name="moj_tool",
            description="Čo tool robí",
            parameters={"type": "object", "properties": {"arg": {"type": "string"}}, "required": ["arg"]},
            handler=self.moj_handler,
        ))

    async def moj_handler(self, arg: str) -> dict:
        return {"result": f"Spracované: {arg}"}

# Použitie
router = AIRouter(config)
agent = MojAgent("moj-agent", router)
run = await agent.run("urob niečo s argumentom X")
print(run.final_output)
```

---

## Licencia

MIT — rob s tým čo chceš.
