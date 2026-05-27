#!/usr/bin/env bash
# AIOS KDE klávesové skratky — spusti raz po prvom prihlásení
# Nastaví globálne skratky cez kwriteconfig5

set -euo pipefail

KGLOBALSHORTCUTS="$HOME/.config/kglobalshortcutsrc"

# Super+A — toggle AIOS panel
kwriteconfig5 --file kglobalshortcutsrc \
    --group "aios-panel.desktop" \
    --key "_launch" "Meta+A,none,AIOS Panel"

# Super+T — terminál (Konsole)
kwriteconfig5 --file kglobalshortcutsrc \
    --group "org.kde.konsole.desktop" \
    --key "_launch" "Meta+T,none,Terminál"

# Aplikuj skratky bez restartu
qdbus org.kde.kglobalaccel /kglobalaccel org.kde.KGlobalAccel.reloadConfig 2>/dev/null || true

echo "AIOS klávesové skratky nastavené:"
echo "  Super+A = AIOS Panel"
echo "  Super+T = Terminál"
