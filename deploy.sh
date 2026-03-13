#!/bin/bash
set -e

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Setup ─────────────────────────────────────────────────────────────────────

cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
nano "$INSTALL_DIR/.env"

python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# ── systemd service ───────────────────────────────────────────────────────────

# Patch service file with actual install path before copying
sed "s|/opt/nexus-sentinel|$INSTALL_DIR|g" \
    "$INSTALL_DIR/nexus-sentinel.service" | \
    sudo tee /etc/systemd/system/nexus-sentinel.service > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now nexus-sentinel

echo "Nexus-sentinel is running. Logs: journalctl -u nexus-sentinel -f"
