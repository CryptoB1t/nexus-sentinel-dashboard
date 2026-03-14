#!/bin/bash
set -e

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
 
# ── Configure .env ────────────────────────────────────────────────────────────
 
echo ""
echo "=== Nexus Sentinel Setup ==="
echo ""
 
read -rp "Telegram Bot Token: " BOT_TOKEN
while [[ -z "$BOT_TOKEN" ]]; do
    echo "Token cannot be empty."
    read -rp "Telegram Bot Token: " BOT_TOKEN
done
 
read -rp "Your Telegram Chat ID: " CHAT_ID
while [[ -z "$CHAT_ID" ]]; do
    echo "Chat ID cannot be empty."
    read -rp "Your Telegram Chat ID: " CHAT_ID
done
 
cat > "$INSTALL_DIR/.env" << EOF
TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
ADMIN_CHAT_ID=${CHAT_ID}
EOF
 
echo ""
echo ".env saved."
 
# ── Python setup ──────────────────────────────────────────────────────────────
 
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
 
# ── systemd service ───────────────────────────────────────────────────────────
 
sed "s|/opt/nexus-sentinel|$INSTALL_DIR|g" \
    "$INSTALL_DIR/nexus-sentinel.service" | \
    sudo tee /etc/systemd/system/nexus-sentinel.service > /dev/null
 
sudo systemctl daemon-reload
sudo systemctl enable --now nexus-sentinel

echo "Nexus-sentinel is running. Logs: journalctl -u nexus-sentinel -f"
