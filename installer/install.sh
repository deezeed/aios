#!/usr/bin/env bash
# AIOS Installer — installs AIOS on top of a base Arch Linux system.
# Run as root after a minimal Arch install.
set -euo pipefail

AIOS_VERSION="0.1.0"
AIOS_USER="${SUDO_USER:-aios}"
AIOS_HOME="/home/${AIOS_USER}"
AIOS_DIR="/opt/aios"
LOG="/tmp/aios-install.log"
DESKTOP="${AIOS_DESKTOP:-}"   # "kde" | "sway" | nastavené interaktívne

# Colors
R='\033[0;31m' G='\033[0;32m' Y='\033[0;33m'
C='\033[0;36m' W='\033[1;37m' RESET='\033[0m'

log()  { echo -e "${G}[AIOS]${RESET} $*" | tee -a "$LOG"; }
warn() { echo -e "${Y}[WARN]${RESET} $*" | tee -a "$LOG"; }
err()  { echo -e "${R}[ERR ]${RESET} $*" | tee -a "$LOG"; exit 1; }
step() { echo -e "\n${C}━━━ $* ━━━${RESET}" | tee -a "$LOG"; }

banner() {
cat << 'EOF'
   _   ___ ___  ___
  /_\ |_ _/ _ \/ __|
 / _ \ | | (_) \__ \
/_/ \_\___\___/|___/  AI Operating System
EOF
echo -e "${C}Version ${AIOS_VERSION}  |  Arch Linux${RESET}\n"
}

check_root() {
    [[ $EUID -eq 0 ]] || err "Run as root: sudo bash install.sh"
}

check_arch() {
    [[ -f /etc/arch-release ]] || err "AIOS requires Arch Linux"
}

# ─── Package installation ────────────────────────────────────────────────────

install_base_packages() {
    step "Updating system and installing base packages"
    pacman -Syu --noconfirm 2>>"$LOG"
    pacman -S --noconfirm --needed \
        base-devel git curl wget unzip \
        python python-pip python-pipx \
        rust cargo \
        neovim vim nano \
        htop btop iotop \
        tmux screen \
        rsync openssh \
        jq yq \
        ripgrep fd bat \
        tree ncdu \
        net-tools iproute2 \
        nmap wireshark-cli tcpdump \
        ufw \
        2>>"$LOG"
    log "Base packages installed"
}

choose_desktop() {
    if [[ -n "$DESKTOP" ]]; then
        return
    fi
    echo ""
    echo -e "${W}Vyber desktop prostredie:${RESET}"
    echo -e "  ${C}1)${RESET} ${W}KDE Plasma${RESET}  — podobné Windowsu, jednoduché ovládanie ${G}(odporúčané)${RESET}"
    echo -e "  ${C}2)${RESET} ${W}Sway (Tiling)${RESET} — pre pokročilých, klávesnicové ovládanie"
    echo ""
    read -rp "Voľba [1/2, default=1]: " choice
    case "${choice:-1}" in
        2) DESKTOP="sway" ;;
        *) DESKTOP="kde" ;;
    esac
    echo -e "${G}Zvolený desktop: ${DESKTOP}${RESET}"
}

install_fonts() {
    log "Installing JetBrains Mono Nerd Font..."
    local font_url="https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.zip"
    local font_dir="/usr/share/fonts/JetBrainsMono"
    mkdir -p "$font_dir"
    curl -sL "$font_url" -o /tmp/jbmono.zip 2>>"$LOG" && \
        unzip -qo /tmp/jbmono.zip -d "$font_dir" && \
        fc-cache -f && \
        log "Font installed" || warn "Font download failed"
}

install_kde_desktop() {
    step "Installing KDE Plasma desktop"
    pacman -S --noconfirm --needed \
        plasma-meta \
        kde-applications-meta \
        sddm \
        alacritty \
        konsole \
        firefox \
        dolphin \
        ark \
        kate \
        spectacle \
        pipewire wireplumber pipewire-pulse \
        pavucontrol \
        network-manager-applet \
        python-pyqt6 \
        noto-fonts noto-fonts-emoji \
        xdg-desktop-portal-kde \
        2>>"$LOG"

    # Štýl — Breeze Dark
    sudo -u "$AIOS_USER" kwriteconfig5 \
        --file kdeglobals --group KDE --key widgetStyle "Breeze" 2>>"$LOG" || true
    sudo -u "$AIOS_USER" kwriteconfig5 \
        --file kdeglobals --group General --key ColorScheme "BreezeDark" 2>>"$LOG" || true

    # Autostart AIOS panel
    local autostart_dir="${AIOS_HOME}/.config/autostart"
    sudo -u "$AIOS_USER" mkdir -p "$autostart_dir"
    sudo -u "$AIOS_USER" cp "${AIOS_DIR}/desktop/kde/aios-autostart.desktop" "$autostart_dir/"

    # Desktop súbor pre spustenie
    cp "${AIOS_DIR}/desktop/kde/aios-panel.desktop" /usr/share/applications/

    systemctl enable sddm
    install_fonts
    log "KDE Plasma installed"
}

install_wayland_desktop() {
    step "Installing Sway (Wayland tiling) desktop"
    pacman -S --noconfirm --needed \
        sway swaybg swaylock swayidle \
        waybar \
        rofi-wayland \
        mako \
        alacritty \
        firefox \
        grim slurp wl-clipboard \
        xdg-desktop-portal-wlr \
        polkit polkit-gnome \
        pipewire wireplumber pipewire-pulse \
        pavucontrol \
        brightnessctl \
        network-manager-applet \
        noto-fonts noto-fonts-emoji \
        2>>"$LOG"

    install_fonts
    log "Sway desktop installed"
}

install_desktop() {
    if [[ "$DESKTOP" == "kde" ]]; then
        install_kde_desktop
    else
        install_wayland_desktop
    fi
}

install_docker() {
    step "Installing Docker and Docker Compose"
    pacman -S --noconfirm --needed docker docker-compose 2>>"$LOG"
    systemctl enable --now docker
    usermod -aG docker "$AIOS_USER"
    log "Docker installed and enabled"

    # lazydocker — nice TUI for Docker
    local ld_url="https://github.com/jesseduffield/lazydocker/releases/latest/download/lazydocker_Linux_x86_64.tar.gz"
    curl -sL "$ld_url" | tar xz -C /usr/local/bin lazydocker 2>>"$LOG" && \
        chmod +x /usr/local/bin/lazydocker && \
        log "lazydocker installed" || warn "lazydocker install failed"
}

install_kubernetes() {
    step "Installing Kubernetes tools"
    # kubectl
    local k8s_ver
    k8s_ver=$(curl -sL https://dl.k8s.io/release/stable.txt 2>>"$LOG")
    curl -sLO "https://dl.k8s.io/release/${k8s_ver}/bin/linux/amd64/kubectl" 2>>"$LOG"
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
    rm kubectl

    # helm
    curl -sL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash 2>>"$LOG"

    # k9s — Kubernetes TUI
    local k9s_url="https://github.com/derailed/k9s/releases/latest/download/k9s_Linux_amd64.tar.gz"
    curl -sL "$k9s_url" | tar xz -C /usr/local/bin k9s 2>>"$LOG" && \
        log "k9s installed" || warn "k9s install failed"

    log "Kubernetes tools installed"
}

install_dev_tools() {
    step "Installing development tools"
    pacman -S --noconfirm --needed \
        go \
        nodejs npm \
        terraform \
        ansible \
        vault \
        python-pytest \
        2>>"$LOG"

    # Install common Python dev tools
    pipx install ruff black mypy pytest httpx 2>>"$LOG" || true

    log "Dev tools installed"
}

install_ollama() {
    step "Installing Ollama (local LLM runtime)"
    if command -v ollama &>/dev/null; then
        log "Ollama already installed"
        return
    fi
    curl -fsSL https://ollama.ai/install.sh | sh 2>>"$LOG"
    systemctl enable --now ollama

    log "Pulling default models (this may take a while)..."
    sudo -u "$AIOS_USER" ollama pull llama3.2:3b 2>>"$LOG" &
    sudo -u "$AIOS_USER" ollama pull qwen2.5-coder:7b 2>>"$LOG" &
    sudo -u "$AIOS_USER" ollama pull nomic-embed-text 2>>"$LOG" &
    wait
    log "Ollama installed and models pulled"
}

install_python_deps() {
    step "Installing AIOS Python dependencies"
    pip install --upgrade pip 2>>"$LOG"
    pip install \
        anthropic \
        fastapi \
        uvicorn[standard] \
        httpx \
        websockets \
        tomllib \
        pydantic \
        faster-whisper \
        pyaudio \
        tiktoken \
        numpy \
        redis \
        2>>"$LOG"

    # Try to install Piper TTS
    pip install piper-tts 2>>"$LOG" || \
        warn "piper-tts install failed, will use espeak fallback"

    # openWakeWord (optional)
    pip install openwakeword 2>>"$LOG" || \
        warn "openwakeword install failed, using keyword fallback"

    log "Python dependencies installed"
}

install_piper_voice() {
    step "Installing Piper TTS voice models"
    local voice_dir="${AIOS_HOME}/.local/share/piper/voices"
    mkdir -p "$voice_dir"

    # Slovak voice
    local sk_model="sk_SK-lili-medium"
    local piper_base="https://huggingface.co/rhasspy/piper-voices/resolve/main/sk/sk_SK/lili/medium"
    curl -sL "${piper_base}/${sk_model}.onnx" -o "${voice_dir}/${sk_model}.onnx" 2>>"$LOG" && \
    curl -sL "${piper_base}/${sk_model}.onnx.json" -o "${voice_dir}/${sk_model}.onnx.json" 2>>"$LOG" && \
        log "Slovak voice installed" || warn "Slovak voice download failed"

    # English fallback
    local en_model="en_US-ryan-high"
    local en_base="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high"
    curl -sL "${en_base}/${en_model}.onnx" -o "${voice_dir}/${en_model}.onnx" 2>>"$LOG" && \
    curl -sL "${en_base}/${en_model}.onnx.json" -o "${voice_dir}/${en_model}.onnx.json" 2>>"$LOG" && \
        log "English voice installed" || warn "English voice download failed"

    chown -R "${AIOS_USER}:${AIOS_USER}" "${voice_dir}"
}

install_aios() {
    step "Installing AIOS system files"
    mkdir -p "$AIOS_DIR"
    cp -r /tmp/aios/. "$AIOS_DIR/" 2>>"$LOG" || {
        log "Copying from local source directory"
        # If running from cloned repo, use current dir
        local src_dir
        src_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
        cp -r "${src_dir}/." "$AIOS_DIR/"
    }

    # Install aios CLI
    cp "${AIOS_DIR}/scripts/aios" /usr/local/bin/aios
    chmod +x /usr/local/bin/aios

    # Install wallpaper
    mkdir -p /usr/share/aios
    cp "${AIOS_DIR}/assets/wallpaper.png" /usr/share/aios/ 2>>/dev/null || true

    chown -R "${AIOS_USER}:${AIOS_USER}" "$AIOS_DIR"
    log "AIOS installed to $AIOS_DIR"
}

setup_configs() {
    step "Setting up AIOS configuration"

    mkdir -p /etc/aios
    cp "${AIOS_DIR}/config/aios.toml" /etc/aios/aios.toml

    local cfg="${AIOS_HOME}/.config"

    if [[ "$DESKTOP" == "sway" ]]; then
        sudo -u "$AIOS_USER" mkdir -p \
            "${cfg}/sway" "${cfg}/waybar" "${cfg}/alacritty" \
            "${cfg}/mako" "${cfg}/rofi"
        sudo -u "$AIOS_USER" cp "${AIOS_DIR}/desktop/compositor/sway.config" "${cfg}/sway/config"
        sudo -u "$AIOS_USER" cp "${AIOS_DIR}/desktop/panel/waybar.config" "${cfg}/waybar/config"
        sudo -u "$AIOS_USER" cp "${AIOS_DIR}/desktop/panel/waybar.css" "${cfg}/waybar/style.css"
    else
        sudo -u "$AIOS_USER" mkdir -p "${cfg}/alacritty"
        # Nastav klávesové skratky KDE
        sudo -u "$AIOS_USER" bash "${AIOS_DIR}/desktop/kde/kde-shortcuts.sh" 2>>"$LOG" || true
    fi

    # Alacritty config (shared)
    cat > "${cfg}/alacritty/alacritty.toml" << 'ALACRITTY_EOF'
[window]
opacity = 0.92
padding = { x = 10, y = 10 }
decorations = "none"

[font]
normal = { family = "JetBrains Mono Nerd Font", style = "Regular" }
size = 12.0

[colors.primary]
background = "#121212"
foreground = "#cdd6f4"

[colors.normal]
black   = "#1c2026"
red     = "#f44336"
green   = "#66bb6a"
yellow  = "#ffca28"
blue    = "#42a5f5"
magenta = "#ab47bc"
cyan    = "#26c6da"
white   = "#b0bec5"

[colors.bright]
cyan    = "#00bcd4"
white   = "#eceff1"
ALACRITTY_EOF

    chown -R "${AIOS_USER}:${AIOS_USER}" "${cfg}"
    log "Configs installed"
}

setup_systemd() {
    step "Setting up systemd services"

    # aiosd.service
    cat > /etc/systemd/system/aiosd.service << SERVICE_EOF
[Unit]
Description=AIOS AI Daemon
After=network.target ollama.service
Wants=ollama.service

[Service]
Type=simple
User=${AIOS_USER}
WorkingDirectory=${AIOS_DIR}
ExecStart=/usr/bin/python3 -m core.daemon.aiosd
Restart=on-failure
RestartSec=5
Environment=PYTHONPATH=${AIOS_DIR}
EnvironmentFile=-/etc/aios/environment
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE_EOF

    # Environment file for secrets
    cat > /etc/aios/environment << ENV_EOF
# AIOS environment variables
# Fill in your API keys here
ANTHROPIC_API_KEY=
AIOS_HOST=127.0.0.1
AIOS_PORT=7474
ENV_EOF
    chmod 600 /etc/aios/environment
    chown "${AIOS_USER}:${AIOS_USER}" /etc/aios/environment

    systemctl daemon-reload
    systemctl enable aiosd
    log "systemd services configured"
}

setup_sddm() {
    step "Setting up display manager (SDDM)"
    # KDE install already handles SDDM; Sway needs manual setup
    if [[ "$DESKTOP" == "sway" ]]; then
        pacman -S --noconfirm --needed sddm 2>>"$LOG"
        systemctl enable sddm

        mkdir -p /usr/share/wayland-sessions
        cat > /usr/share/wayland-sessions/sway-aios.desktop << 'SESSION_EOF'
[Desktop Entry]
Name=AIOS (Sway)
Comment=AI Operating System — Sway Wayland
Exec=sway
Type=Application
SESSION_EOF
    fi
    log "Display manager configured"
}

print_summary() {
    echo ""
    echo -e "${C}══════════════════════════════════════════${RESET}"
    echo -e "${W}  AIOS Installation Complete!${RESET}"
    echo -e "${C}══════════════════════════════════════════${RESET}"
    echo ""
    echo -e "  ${G}✓${RESET} Base system + Wayland desktop"
    echo -e "  ${G}✓${RESET} Docker + Kubernetes tools"
    echo -e "  ${G}✓${RESET} Ollama (local LLM runtime)"
    echo -e "  ${G}✓${RESET} Voice module (Whisper + Piper)"
    echo -e "  ${G}✓${RESET} AIOS daemon (aiosd)"
    echo -e "  ${G}✓${RESET} AIOS CLI (/usr/local/bin/aios)"
    echo ""
    echo -e "  ${Y}Next steps:${RESET}"
    echo -e "  1. Edit ${W}/etc/aios/environment${RESET} and add your ANTHROPIC_API_KEY"
    echo -e "  2. Reboot and log in to the AIOS session"
    echo -e "  3. Open terminal and run: ${W}aios shell${RESET}"
    echo -e "  4. Say '${W}aios${RESET}' to activate voice mode"
    echo ""
    echo -e "  ${DIM}Log: $LOG${RESET}"
    echo ""
}

# ─── Main ─────────────────────────────────────────────────────────────────────
main() {
    banner
    check_root
    check_arch
    choose_desktop

    install_base_packages
    install_desktop
    install_docker
    install_kubernetes
    install_dev_tools
    install_ollama
    install_python_deps
    install_piper_voice
    install_aios
    setup_configs
    setup_systemd
    setup_sddm

    print_summary
}

main "$@"
