#!/usr/bin/env python3
"""
Nexus Ultimate Dashboard & Self-Healing Sentinel

Premium Telegram UX + per-ID precise monitoring + smart cooldown system.
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import psutil
import telebot
from dotenv import load_dotenv
from telebot import types

# Load .env file
load_dotenv()


# --- Configuration -----------------------------------------------------------

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_CHAT_ID: str = os.environ["ADMIN_CHAT_ID"]

_DATA_DIR = os.environ.get("DATA_DIR", "data")
LOG_FILE = os.path.join(_DATA_DIR, "nexus_watch.log")
NODES_FILE = os.path.join(_DATA_DIR, "nodes_list.txt")
SETTINGS_FILE = os.path.join(_DATA_DIR, "settings.json")

CHECK_INTERVAL_SECONDS: int = int(os.environ.get("CHECK_INTERVAL_SECONDS", "30"))
COOLDOWN_SECONDS: int = int(os.environ.get("COOLDOWN_SECONDS", "600"))

RESTART_CMD_PREFIX = ["screen", "-dmS"]

NEXUS_PATH: str = os.environ.get("NEXUS_PATH", "/root/.nexus/bin/nexus-network")

# Ensure data directory exists at startup
os.makedirs(_DATA_DIR, exist_ok=True)


# --- Logging ----------------------------------------------------------------

def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# --- Markdown V2 escaping ----------------------------------------------------

_MDV2_SPECIAL = r"_*[]()~`>#+-=|{}.!\\"


def mdv2_escape(text: str) -> str:
    return re.sub(rf"([{re.escape(_MDV2_SPECIAL)}])", r"\\\1", text)


def mdv2_bold(text: str) -> str:
    return f"*{mdv2_escape(text)}*"


def mdv2_code(text: str) -> str:
    return f"`{mdv2_escape(text)}`"


DIVIDER = mdv2_escape("-------------------------")


# --- Storage (nodes_list.txt) ------------------------------------------------

_file_lock = threading.Lock()


def ensure_nodes_file_exists() -> None:
    if not os.path.exists(NODES_FILE):
        with open(NODES_FILE, "w", encoding="utf-8"):
            pass


def load_nodes() -> List[str]:
    ensure_nodes_file_exists()
    with _file_lock:
        with open(NODES_FILE, "r", encoding="utf-8") as f:
            nodes = [line.strip() for line in f if line.strip()]
    return sorted(set(nodes))


def save_nodes(nodes: List[str]) -> None:
    ensure_nodes_file_exists()
    unique_nodes = sorted(set(nodes))
    with _file_lock:
        with open(NODES_FILE, "w", encoding="utf-8") as f:
            for nid in unique_nodes:
                f.write(f"{nid}\n")


def add_node(node_id: str) -> Tuple[bool, str]:
    node_id = node_id.strip()
    if not node_id:
        return False, "Empty node ID."
    nodes = load_nodes()
    if node_id in nodes:
        return False, "Node already exists."
    nodes.append(node_id)
    save_nodes(nodes)
    logging.info("Added node %s to %s", node_id, NODES_FILE)
    return True, "Node added."


def remove_node(node_id: str) -> Tuple[bool, str]:
    node_id = node_id.strip()
    nodes = load_nodes()
    if node_id not in nodes:
        return False, "Node not found."
    nodes = [n for n in nodes if n != node_id]
    save_nodes(nodes)
    logging.info("Removed node %s from %s", node_id, NODES_FILE)
    return True, "Node removed."


# --- Settings (settings.json) ------------------------------------------------

_settings_lock = threading.Lock()


def load_settings() -> Dict[str, object]:
    """
    Load bot settings from SETTINGS_FILE.

    Keys:
    - notifications_enabled: bool
    """
    default: Dict[str, object] = {"notifications_enabled": True}
    if not os.path.exists(SETTINGS_FILE):
        return default

    try:
        with _settings_lock:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        if not isinstance(data, dict):
            return default
        merged: Dict[str, object] = {**default, **data}
        merged["notifications_enabled"] = bool(merged.get("notifications_enabled", True))
        return merged
    except Exception as exc:  # noqa: BLE001
        logging.error("Failed to load settings: %s", exc)
        return default


def save_settings(settings: Dict[str, object]) -> None:
    try:
        with _settings_lock:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        logging.error("Failed to save settings: %s", exc)


def get_notifications_enabled() -> bool:
    return bool(load_settings().get("notifications_enabled", True))


def set_notifications_enabled(enabled: bool) -> None:
    settings = load_settings()
    settings["notifications_enabled"] = bool(enabled)
    save_settings(settings)


# --- Per-ID monitoring (ps aux) -----------------------------------------------

def is_node_running(node_id: str) -> bool:
    """
    Node is ONLINE if ps aux has a line containing BOTH "nexus" AND node_id.
    Works even when node runs without screen.
    """
    node_id = str(node_id).strip()
    if not node_id:
        return False

    try:
        result = subprocess.run(
            "ps aux | grep nexus",
            shell=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        ps_output = (result.stdout or "") + (result.stderr or "")
    except Exception:  # noqa: BLE001
        ps_output = ""

    for line in ps_output.splitlines():
        if "monitor.py" in line:
            continue
        if "nexus" in line.lower() and node_id in line:
            return True

    return False


def is_node_online(node_id: str) -> bool:
    """Wrapper used by the rest of the code."""
    return is_node_running(node_id)


def restart_node(node_id: str) -> Tuple[bool, str]:
    cmd = [
        *RESTART_CMD_PREFIX,
        f"nexus_{node_id}",
        NEXUS_PATH,
        "start",
        "--node-id",
        node_id,
    ]
    try:
        logging.warning("Restarting node %s with: %s", node_id, " ".join(cmd))
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True, "Restart command executed."
    except FileNotFoundError:
        return False, "screen or nexus-network binary not found."
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else str(exc)
        return False, f"Restart failed: {stderr}"


def get_system_resources() -> Dict[str, str]:
    cpu_percent = psutil.cpu_percent(interval=1)
    vm = psutil.virtual_memory()
    ram_used_mb = vm.used / (1024 * 1024)
    ram_total_mb = vm.total / (1024 * 1024)

    disk = psutil.disk_usage("/")
    free_gb = disk.free / (1024 * 1024 * 1024)
    return {
        "cpu": f"{cpu_percent:.1f}%",
        "ram": f"{ram_used_mb:.0f}/{ram_total_mb:.0f} MB",
        "disk_free": f"{free_gb:.2f} GB",
    }


# --- Log reading -------------------------------------------------------------

EMPTY_LOGS_MSG = "📋 Logs are currently empty."

# View Logs: try nexus node log first, then bot log in data folder
_LOG_SOURCES = [
    "/root/.nexus/network-api/nexus.log",
    LOG_FILE,
]


def _get_log_file_for_read() -> Optional[str]:
    """First existing log file: nexus.log or nexus_watch.log in data folder."""
    for path in _LOG_SOURCES:
        if os.path.isfile(path):
            return path
    return None


def get_logs(node_id: Optional[str] = None, max_lines: int = 15) -> str:
    """
    Read last max_lines from log file.
    Tries /root/.nexus/network-api/nexus.log, then nexus_watch.log in data folder.
    If node_id given, filter lines containing that ID.
    If file missing or empty, return EMPTY_LOGS_MSG.
    """
    log_path = _get_log_file_for_read()
    if not log_path:
        return EMPTY_LOGS_MSG
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as exc:  # noqa: BLE001
        return f"Failed to read log: {exc}"

    if node_id:
        filtered = [ln for ln in lines if node_id in ln]
        tail = filtered[-max_lines:] if filtered else lines[-max_lines:]
    else:
        tail = lines[-max_lines:]

    result = "".join(tail).strip()
    return result if result else EMPTY_LOGS_MSG


def read_log_tail(max_lines: int = 15) -> str:
    """Alias for get_logs() without node filter."""
    return get_logs(node_id=None, max_lines=max_lines)


def read_node_log_snippet(node_id: str, max_lines: int = 15) -> str:
    """Return last max_lines of log entries containing node_id."""
    return get_logs(node_id=node_id, max_lines=max_lines)


# --- Smart Cooldown ----------------------------------------------------------

@dataclass
class CooldownState:
    until_ts: float = 0.0
    last_alert_ts: float = 0.0


cooldowns: Dict[str, CooldownState] = {}


def is_in_cooldown(node_id: str, now: float) -> bool:
    state = cooldowns.get(node_id)
    return bool(state and now < state.until_ts)


def start_cooldown(node_id: str, now: float) -> None:
    state = cooldowns.get(node_id) or CooldownState()
    state.until_ts = now + COOLDOWN_SECONDS
    cooldowns[node_id] = state


# --- Telegram Bot (pyTelegramBotAPI) -----------------------------------------

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode="MarkdownV2", threaded=True)


def is_admin(message_or_call) -> bool:
    try:
        chat_id = message_or_call.chat.id  # message
    except Exception:
        chat_id = message_or_call.message.chat.id  # callback
    return int(chat_id) == int(ADMIN_CHAT_ID)


def deny_if_not_admin(message_or_call) -> bool:
    if is_admin(message_or_call):
        return False
    try:
        bot.reply_to(
            message_or_call,
            mdv2_bold("Access denied") + "\n" + mdv2_escape("This bot is private."),
        )
    except Exception:
        pass
    return True


def main_menu_markup() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    notif_enabled = get_notifications_enabled()
    notif_label = "🔔 Notifications: ON" if notif_enabled else "🔕 Notifications: OFF"
    kb.add(
        types.InlineKeyboardButton("📊 System Stats", callback_data="menu:status"),
        types.InlineKeyboardButton("🆔 My Nodes", callback_data="menu:nodes"),
        types.InlineKeyboardButton("🛠 Settings", callback_data="menu:settings"),
        types.InlineKeyboardButton("📜 View Logs", callback_data="menu:logs"),
    )
    kb.add(types.InlineKeyboardButton(notif_label, callback_data="menu:toggle_notifications"))
    return kb


def alert_actions_markup(node_id: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("♻️ Restart Manually", callback_data=f"node:restart:{node_id}"),
        types.InlineKeyboardButton("📈 View Log", callback_data=f"node:log:{node_id}"),
    )
    return kb


def format_status_message() -> str:
    res = get_system_resources()
    nodes = load_nodes()

    lines = [
        "🛡 " + mdv2_bold("Nexus Ultimate Dashboard"),
        DIVIDER,
        "📊 " + mdv2_bold("System Status"),
        f"• {mdv2_bold('CPU')}: {mdv2_escape(res['cpu'])}",
        f"• {mdv2_bold('RAM')}: {mdv2_escape(res['ram'])}",
        f"• {mdv2_bold('Disk Free')}: {mdv2_escape(res['disk_free'])}",
        DIVIDER,
        "🆔 " + mdv2_bold("Nodes"),
    ]

    if not nodes:
        lines.append(mdv2_escape("No nodes yet. Use /add <NODE_ID>"))
        return "\n".join(lines)

    for nid in nodes:
        online = is_node_online(nid)
        state = "✅ Online" if online else "❌ Offline"
        lines.append(f"• {mdv2_code(nid)} — {mdv2_escape(state)}")

    return "\n".join(lines)


def format_nodes_message() -> str:
    nodes = load_nodes()
    lines = [
        "🆔 " + mdv2_bold("My Nodes"),
        DIVIDER,
    ]
    if not nodes:
        lines.append(mdv2_escape("List is empty. Add with /add <NODE_ID>"))
        return "\n".join(lines)
    for nid in nodes:
        lines.append(f"• {mdv2_code(nid)}")
    lines.append(DIVIDER)
    lines.append(mdv2_escape("Commands: /add <ID> | /remove <ID>"))
    return "\n".join(lines)


def format_logs_message() -> str:
    snippet = read_log_tail(max_lines=15)
    body = mdv2_escape(snippet)
    return "\n".join(
        [
            "📜 " + mdv2_bold("View Logs"),
            DIVIDER,
            body,
        ]
    )


def is_integer(s):
    try:
        int(s)
        return True
    except ValueError:
        return False


@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message) -> None:
    if deny_if_not_admin(message):
        return
    text = "\n".join(
        [
            "🛡 " + mdv2_bold("Nexus Ultimate Dashboard & Self-Healing Sentinel"),
            DIVIDER,
            mdv2_escape("Choose an action from the menu below."),
            mdv2_escape("Critical for Nexus Global Community."),
        ]
    )
    bot.send_message(message.chat.id, text, reply_markup=main_menu_markup())


@bot.message_handler(commands=["add"])
def cmd_add(message: types.Message) -> None:
    if deny_if_not_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 8 and is_integer(parts):
        bot.reply_to(message, mdv2_escape("Usage: /add <ID>"))
        return
    
    node_id = parts[1].strip()
    ok, info = add_node(node_id)
    status = "✅" if ok else "ℹ️"
    bot.reply_to(
        message,
        "\n".join(
            [
                f"{status} " + mdv2_bold("Nodes"),
                DIVIDER,
                mdv2_escape(info),
                mdv2_escape("ID: ") + mdv2_code(node_id),
            ]
        ),
    )


@bot.message_handler(commands=["remove"])
def cmd_remove(message: types.Message) -> None:
    if deny_if_not_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 8:
        bot.reply_to(message, mdv2_escape("Usage: /remove <ID>"))
        return
    node_id = parts[1].strip()
    ok, info = remove_node(node_id)
    status = "✅" if ok else "❌"
    bot.reply_to(
        message,
        "\n".join(
            [
                f"{status} " + mdv2_bold("Nodes"),
                DIVIDER,
                mdv2_escape(info),
                mdv2_escape("ID: ") + mdv2_code(node_id),
            ]
        ),
    )


@bot.message_handler(commands=["status"])
def cmd_status(message: types.Message) -> None:
    if deny_if_not_admin(message):
        return
    bot.send_message(message.chat.id, format_status_message(), reply_markup=main_menu_markup())


@bot.callback_query_handler(func=lambda call: True)
def on_callback(call: types.CallbackQuery) -> None:
    if deny_if_not_admin(call):
        return

    data = call.data or ""

    try:
        if data == "menu:status":
            bot.answer_callback_query(call.id)
            bot.edit_message_text(
                format_status_message(),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=main_menu_markup(),
            )
        elif data == "menu:nodes":
            bot.answer_callback_query(call.id)
            bot.edit_message_text(
                format_nodes_message(),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=main_menu_markup(),
            )
        elif data == "menu:settings":
            bot.answer_callback_query(call.id)
            nodes_file_esc = mdv2_escape(NODES_FILE)
            log_file_esc = mdv2_escape(LOG_FILE)
            text = "\n".join(
                [
                    "🛠 " + mdv2_bold("Settings"),
                    DIVIDER,
                    mdv2_escape(f"Check interval: {CHECK_INTERVAL_SECONDS}s"),
                    mdv2_escape(f"Cooldown per node: {COOLDOWN_SECONDS}s"),
                    mdv2_escape("Nodes file: ") + nodes_file_esc,
                    mdv2_escape("Log file: ") + log_file_esc,
                ]
            )
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=main_menu_markup(),
            )
        elif data == "menu:logs":
            bot.answer_callback_query(call.id)
            bot.edit_message_text(
                format_logs_message(),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=main_menu_markup(),
            )
        elif data == "menu:toggle_notifications":
            currently = get_notifications_enabled()
            new_value = not currently
            set_notifications_enabled(new_value)
            state_text = "ON" if new_value else "OFF"
            bot.answer_callback_query(call.id, text=f"Notifications: {state_text}")

            bot.edit_message_text(
                format_status_message(),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=main_menu_markup(),
            )
            if new_value:
                bot.send_message(call.message.chat.id, format_status_message(), reply_markup=main_menu_markup())
        elif data.startswith("node:restart:"):
            node_id = data.split(":", 2)[2]
            ok, info = restart_node(node_id)
            bot.answer_callback_query(call.id, text=info[:100] if info else "Done")
            status = "✅" if ok else "❌"
            text = "\n".join(
                [
                    "♻️ " + mdv2_bold("Manual Restart"),
                    DIVIDER,
                    mdv2_escape(f"{status} {info}"),
                    mdv2_escape("Node: ") + mdv2_code(node_id),
                ]
            )
            bot.send_message(call.message.chat.id, text, reply_markup=main_menu_markup())
        elif data.startswith("node:log:"):
            node_id = data.split(":", 2)[2]
            snippet = read_node_log_snippet(node_id=node_id, max_lines=15)
            bot.answer_callback_query(call.id)
            text = "\n".join(
                [
                    "📈 " + mdv2_bold("Node Log"),
                    DIVIDER,
                    mdv2_escape("Node: ") + mdv2_code(node_id),
                    DIVIDER,
                    mdv2_escape(snippet),
                ]
            )
            bot.send_message(call.message.chat.id, text, reply_markup=main_menu_markup())
        else:
            bot.answer_callback_query(call.id, text="Unknown action.")
    except Exception as exc:  # noqa: BLE001
        logging.exception("Callback error: %s", exc)
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass


# --- Monitoring thread (self-healing + cooldown) -----------------------------

def send_down_alert(node_id: str) -> None:
    if not get_notifications_enabled():
        logging.info("Notifications OFF. Suppressing DOWN alert for node %s", node_id)
        return
    alert_text = "\n".join(
        [
            "⚠️ " + mdv2_bold("ALERT: Node is DOWN! Attempting auto-restart..."),
            DIVIDER,
            mdv2_escape("Node: ") + mdv2_code(node_id),
        ]
    )
    bot.send_message(ADMIN_CHAT_ID, alert_text, reply_markup=alert_actions_markup(node_id))


def monitor_loop() -> None:
    logging.info("Monitoring thread started.")
    while True:
        now = time.time()
        try:
            nodes = load_nodes()
            for node_id in nodes:
                online = is_node_online(node_id)
                if online:
                    logging.info("Node %s: ONLINE", node_id)
                    continue

                if is_in_cooldown(node_id, now):
                    logging.warning("Node %s: DOWN (cooldown active)", node_id)
                    continue

                logging.warning("Node %s: DOWN (alert + restart + cooldown)", node_id)
                send_down_alert(node_id)
                ok, info = restart_node(node_id)
                logging.warning("Node %s restart result: %s | %s", node_id, ok, info)
                start_cooldown(node_id, now)
        except Exception as exc:  # noqa: BLE001
            logging.exception("Monitor loop error: %s", exc)

        time.sleep(CHECK_INTERVAL_SECONDS)


def main() -> None:
    setup_logging()

    print("🛡 Nexus Ultimate Dashboard & Self-Healing Sentinel is online.")
    logging.info("Bot starting (admin_chat_id=%s, NEXUS_PATH=%s).", ADMIN_CHAT_ID, NEXUS_PATH)

    ensure_nodes_file_exists()

    t = threading.Thread(target=monitor_loop, name="nexus-monitor", daemon=True)
    t.start()

    bot.infinity_polling(timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
