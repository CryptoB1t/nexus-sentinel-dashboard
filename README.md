# 🛡️ Nexus Testnet III: Ultimate Sentinel & Dashboard

[![License: MIT](https://img.shields.io)](https://opensource.org)

**Nexus Sentinel** is a professional, open-source Telegram management suite designed specifically for the final sprint of **Nexus Testnet III**. It ensures your nodes maintain 100% uptime through intelligent monitoring and autonomous self-healing.

## 📸 Dashboard Preview


## 🚀 Key Features
- **✅ Smart Monitoring:** Real-time process tracking by Node ID using `ps aux` and `screen -ls`.
- **♻️ Autonomous Self-Healing:** Automatically detects crashes and restarts nodes using precise binary paths.
- **📊 Remote Dashboard:** Monitor CPU, RAM, and Disk health directly from your Telegram chat.
- **📜 Live Log Access:** View the last 15 lines of your node logs with a single click.
- **🔒 Secure & Private:** Admin-only access via Telegram Chat ID protection.

## 🛠️ Quick Start
1. Clone this repo to your VPS.
2. Install dependencies: `pip install pyTelegramBotAPI psutil requests`.
3. Edit `monitor.py` and add your `BOT_TOKEN` and `CHAT_ID`.
4. Run: `python3 monitor.py`

---
*Developed for Nexus Testnet III — Helping the community reach the final milestone!* 🚀
