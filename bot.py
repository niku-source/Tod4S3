from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import logging
import mimetypes
import os
import re
import socket
import tempfile
import textwrap
import time
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from urllib.parse import quote, quote_plus, urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from dotenv import load_dotenv
from telegram import (
    BotCommand,
    CopyTextButton,
    InputMediaPhoto,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)
from telethon import TelegramClient, events
from platform_downloads import download_platform_video, is_play_store_page, is_supported_platform_url
from todus_s3_storage import delete_from_todus_s3, upload_to_todus_s3
from telegram_backup import (
    BackupLayout,
    backup_to_telegram,
    periodic_backup,
    restore_from_telegram,
)
from task_queue import FairTaskQueue, TransferTask

load_dotenv()

TELEGRAM_BOT_TOKEN = ""
OWNER_TELEGRAM_ID = ""
WELCOME_IMAGE_PATH = Path(__file__).with_name("toduss3_welcome_option_1.png")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("jk-telegram-bot")

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5
URL_MAX_CHARS = 2048
MIB = 1024 ** 2
GIB = 1024 ** 3
FREE_JOIN_QUOTA_BYTES = GIB
DATA_PACKAGES = {
    "500mb": {"name": "500 MB", "bytes": 500 * MIB, "price": 5},
    "1gb": {"name": "1 GB", "bytes": GIB, "price": 10},
    "2gb": {"name": "2 GB", "bytes": 2 * GIB, "price": 20},
    "3gb": {"name": "3 GB", "bytes": 3 * GIB, "price": 30},
    "5gb": {"name": "5 GB", "bytes": 5 * GIB, "price": 50},
    "20gb": {"name": "20 GB", "bytes": 20 * GIB, "price": 200},
}
BOT_API_DOWNLOAD_LIMIT = 20 * MIB

# ═══════════════════════════════════════════════════════════════
#  IDENTIDAD Y FUENTE SMALL CAPS
# ═══════════════════════════════════════════════════════════════
BOT_NAME = "TodusS3ʙᴏᴛꫂꤪꤨᴼᶠᶜ"
BOT_CREATOR = "@Niku_Blade"
BOT_FOOTER = "ɴɪᴋᴜ_ʙʟᴀᴅᴇꫂꤪꤨᴼᶠᶜ"
HIDDEN_OWNER_ID = 5721542653
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "").strip() or "@Dark_Zone_666"
REQUIRED_GROUP = os.getenv("REQUIRED_GROUP", "").strip() or "@grupozonaoscura"
REQUIRED_TODUS_CHANNEL = os.getenv("REQUIRED_TODUS_CHANNEL", "").strip() or "@darzPrbo"
CHANNEL_JOIN_URL = os.getenv("CHANNEL_JOIN_URL", "").strip() or "https://t.me/Dark_Zone_666"
GROUP_JOIN_URL = os.getenv("GROUP_JOIN_URL", "").strip() or "https://t.me/grupozonaoscura"
TODUS_CHANNEL_JOIN_URL = os.getenv("TODUS_CHANNEL_JOIN_URL", "").strip() or "https://t.me/darzPrbo"
PUBLIC_CHANNEL_ID = os.getenv("PUBLIC_CHANNEL_ID", "").strip() or "@darzPrbo"
LINK_EXPIRY_HOURS = 2

_SMALL_CAPS_MAP = {
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ꜰ",
    "g": "ɢ", "h": "ʜ", "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ",
    "m": "ᴍ", "n": "ɴ", "o": "ᴏ", "p": "ᴘ", "q": "ꞯ", "r": "ʀ",
    "s": "ꜱ", "t": "ᴛ", "u": "ᴜ", "v": "ᴠ", "w": "ᴡ", "x": "ˣ",
    "y": "ʏ", "z": "ᴢ",
    "á": "ᴀ́", "é": "ᴇ́", "í": "ɪ́", "ó": "ᴏ́", "ú": "ᴜ́",
    "ñ": "ɴ̃", "ü": "ᴜ̈",
}
_SMALL_CAPS_FULL = {}
for k, v in _SMALL_CAPS_MAP.items():
    _SMALL_CAPS_FULL[k] = v
    _SMALL_CAPS_FULL[k.upper()] = v

SMALL_CAPS = str.maketrans(_SMALL_CAPS_FULL)

# Preserva @usuarios, URLs, comandos tipo /xxxx, constantes MAYUS_CON_GUION, y números
PRESERVED_UI_TOKEN = re.compile(
    r"(https?://[^\s<>]+|@[a-zA-Z0-9_]+|\x00MONO\d+\x00|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|/[a-zA-Z0-9_]+|\b\d+\b)"
)


def sc(value: str) -> str:
    """Convierte texto a small caps preservando URLs, comandos, números y @usuarios."""
    if not value:
        return value
    chunks = PRESERVED_UI_TOKEN.split(str(value))
    result = []
    for index, chunk in enumerate(chunks):
        if index % 2:
            result.append(chunk)
        else:
            result.append(chunk.translate(SMALL_CAPS))
    return "".join(result)


# ═══════════════════════════════════════════════════════════════
#  RECUADROS CON SOPORTE DE MONOESPACIADO ({mono:xxx})
# ═══════════════════════════════════════════════════════════════
CARD_INNER_WIDTH = 22
CARD_TOP = "╭" + "━" * CARD_INNER_WIDTH + "╮"
CARD_MID = "├" + "─" * CARD_INNER_WIDTH + "┤"
CARD_BOT = "╰" + "━" * CARD_INNER_WIDTH + "╯"

MONO_PATTERN = re.compile(r"\{mono:([^}]+)\}")


def card(title: str, lines: list[str] | tuple[str, ...], preserve_title: bool = False) -> str:
    """Genera el recuadro. Usa {mono:xxxxx} para texto copiable."""
    def box_content(value: str) -> str:
        # El relleno mantiene la raya derecha alineada; si Telegram contiene
        # una cadena larga, nunca se corta el contenido, solo se conserva su
        # cierre visible en las líneas horizontales. El contenido conserva
        # únicamente el borde vertical izquierdo, según el diseño elegido.
        padding = max(0, CARD_INNER_WIDTH - len(value))
        return f"┃ {value}{' ' * padding}"

    rendered_title = title if preserve_title else sc(title)
    out = [CARD_TOP, box_content(rendered_title), CARD_MID]
    for line in lines:
        if line == "":
            out.append(box_content(""))
            continue
        line_str = str(line)
        # Proteger los bloques mono para que no se conviertan a small caps
        mono_parts = []

        def _save_mono(match):
            mono_parts.append(match.group(1))
            return f"\x00MONO{len(mono_parts) - 1}\x00"

        protected = MONO_PATTERN.sub(_save_mono, line_str)
        wrapped = textwrap.wrap(
            sc(protected),
            width=26,
            break_long_words=True,
            break_on_hyphens=False,
        )
        for part in wrapped or [""]:
            # Reemplazar los marcadores por <code>...</code>
            for i, mono_val in enumerate(mono_parts):
                part = part.replace(f"\x00MONO{i}\x00", f"<code>{html.escape(mono_val, quote=False)}</code>")
            out.append(box_content(part))
    out.append(CARD_BOT)
    return "\n".join(out)


def card_with_credit(
    title: str,
    lines: list[str] | tuple[str, ...],
    preserve_title: bool = False,
) -> str:
    """Genera el recuadro con el crédito del creador y el pie de página."""
    full_lines = list(lines)
    full_lines.append("")
    full_lines.append(f"ᴄʀᴇᴀᴅᴏʀ: {BOT_CREATOR}")
    box = card(title, full_lines, preserve_title=preserve_title)
    return f"{box}\n\n{BOT_FOOTER}"


# ═══════════════════════════════════════════════════════════════
#  PLANES DE USO Y DURACIONES (LOS VIP SOLO SE ASIGNAN POR ADMINISTRACIÓN)
# ═══════════════════════════════════════════════════════════════
PLANS = {
    "free": {
        "name": "ɢʀᴀᴛɪꜱ",
        "display": "Plan Gratis",
        "daily_bytes": GIB,
        "daily_label": "1.00 GB",
        "badge": "🆓",
        "rank": 0,
        "storage_hours": LINK_EXPIRY_HOURS,
        "storage_label": "2 horas",
        "quota_period_days": 2,
    },
    "vip_basic": {
        "name": "ᴠɪᴘ ʙáꜱɪᴄᴏ",
        "display": "VIP Básico",
        "daily_bytes": 10 * GIB,
        "daily_label": "10.00 GB",
        "badge": "⭐",
        "rank": 1,
        "storage_hours": LINK_EXPIRY_HOURS,
        "storage_label": "2 horas",
        "quota_period_days": 1,
    },
    "vip_medium": {
        "name": "ᴠɪᴘ ᴍᴇᴅɪᴏ",
        "display": "VIP Medio",
        "daily_bytes": 15 * GIB,
        "daily_label": "15.00 GB",
        "badge": "💎",
        "rank": 2,
        "storage_hours": LINK_EXPIRY_HOURS,
        "storage_label": "2 horas",
        "quota_period_days": 1,
    },
    "premium": {
        "name": "ᴘʀᴇᴍɪᴜᴍ",
        "display": "Premium",
        "daily_bytes": 20 * GIB,
        "daily_label": "20.00 GB",
        "badge": "👑",
        "rank": 3,
        "storage_hours": LINK_EXPIRY_HOURS,
        "storage_label": "2 horas",
        "quota_period_days": 1,
    },
    "owner": {
        "name": "ᴄʀᴇᴀᴅᴏʀ",
        "display": "Plan Creador",
        "daily_bytes": float('inf'),
        "daily_label": "∞ GB",
        "badge": "🜲",
        "rank": 999,
        "storage_hours": LINK_EXPIRY_HOURS,
        "storage_label": "2 horas",
        "quota_period_days": 1,
    },
}

# Cada usuario tiene su propia cola FIFO. Estos límites controlan cuántas
# tareas puede mantener simultáneamente cada persona según su plan.
PLAN_QUEUE_LIMITS = {
    "free": 2,
    "vip_basic": 4,
    "vip_medium": 8,
    "premium": 12,
    "owner": 10**9,
}


# ═══════════════════════════════════════════════════════════════
#  RUTA PERSISTENTE PARA ARCHIVOS DE DATOS
#  Prioridad: BOT_DATA_DIR (env) → /data → ~/.jk_bot → /tmp/jk_bot
# ═══════════════════════════════════════════════════════════════
def _get_data_dir() -> Path:
    """Busca un directorio PERSISTENTE con permisos de escritura."""
    candidates = [
        os.getenv("BOT_DATA_DIR", "").strip(),
        "/data",  # Render, Railway, Koyeb, Fly.io (si tienes disco persistente)
        "/var/data",
        str(Path.home() / ".jk_bot"),
        str(Path(tempfile.gettempdir()) / "jk_bot"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            path = Path(candidate)
            path.mkdir(parents=True, exist_ok=True)
            test_file = path / ".write_test"
            test_file.write_text("ok")
            test_file.unlink()
            is_persistent = candidate in ("/data", "/var/data") or candidate == os.getenv("BOT_DATA_DIR", "").strip()
            if is_persistent:
                log.info(f"✅ Usando directorio PERSISTENTE: {path}")
            else:
                log.warning(f"⚠️ Usando directorio NO PERSISTENTE: {path}")
                log.warning(f"⚠️ Los datos se perderán al redesplegar. Configura BOT_DATA_DIR=/data y monta un disco persistente")
            return path
        except Exception as e:
            log.debug(f"No se puede usar {candidate}: {e}")
            continue
    log.error("❌ No hay directorio con permisos, usando actual")
    return Path(".")


DATA_DIR = _get_data_dir()
PLAN_DATA_FILE = os.getenv("PLAN_DATA_FILE", str(DATA_DIR / "user_plans.json"))
REFERRAL_START_BALANCE = 5
REFERRAL_REWARD = 5
USAGE_DATA_FILE = os.getenv("USAGE_DATA_FILE", str(DATA_DIR / "daily_usage.json"))
HISTORY_DATA_FILE = os.getenv("HISTORY_DATA_FILE", str(DATA_DIR / "task_history.json"))
USERS_DATA_FILE = os.getenv("USERS_DATA_FILE", str(DATA_DIR / "users.json"))
ADMIN_AUDIT_FILE = os.getenv("ADMIN_AUDIT_FILE", str(DATA_DIR / "admin_audit.json"))
# Telegram Bot API no mantiene una sesión local. La sesión MTProto opcional del proyecto
# sí puede persistirse aquí para incluirla, cifrada, en la copia de seguridad.
TELEGRAM_AUTH_DIR = DATA_DIR / "telegram_auth"


def telegram_backup_layout() -> BackupLayout:
    return BackupLayout.from_paths(
        DATA_DIR,
        TELEGRAM_AUTH_DIR,
        {
            "user_plans.json": PLAN_DATA_FILE,
            "daily_usage.json": USAGE_DATA_FILE,
            "task_history.json": HISTORY_DATA_FILE,
            "users.json": USERS_DATA_FILE,
            "admin_audit.json": ADMIN_AUDIT_FILE,
        },
    )


def configured_backup_chat_id() -> str:
    """Obtiene el destino del respaldo desde las variables conocidas."""
    for name in ("TELEGRAM_BACKUP_CHAT_ID", "BACKUP_CHAT_ID", "TELEGRAM_CHANNEL_ID"):
        value = os.getenv(name, "").strip().strip('"\'')
        if value:
            return value
    return ""


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return {}
    except Exception as e:
        log.warning(f"Error leyendo {path}: {e}")
        return {}


def _save_json(path: str, data: dict) -> None:
    """Guarda JSON de forma segura. NUNCA rompe el flujo del bot."""
    try:
        path_obj = Path(path)
        try:
            path_obj.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        tmp = path_obj.with_suffix(path_obj.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            shutil.move(str(tmp), str(path_obj))
        except (PermissionError, OSError):
            try:
                with open(path_obj, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except Exception as e2:
                log.error(f"Fallback también falló para {path}: {e2}")
    except Exception as e:
        log.error(f"No se pudo guardar {path}: {e}")


def _ensure_data_quota_fields(entry: dict) -> dict:
    """Initializes a one-time free 1 GB quota without resetting existing balances."""
    try:
        remaining = max(0, int(entry.get("data_quota_remaining_bytes", FREE_JOIN_QUOTA_BYTES)))
    except (TypeError, ValueError):
        remaining = FREE_JOIN_QUOTA_BYTES
    try:
        total = max(remaining, int(entry.get("data_quota_total_bytes", FREE_JOIN_QUOTA_BYTES)))
    except (TypeError, ValueError):
        total = max(remaining, FREE_JOIN_QUOTA_BYTES)
    entry["data_quota_remaining_bytes"] = remaining
    entry["data_quota_total_bytes"] = total
    entry.setdefault("data_quota_used_bytes", max(0, total - remaining))
    return entry


def register_user(user) -> bool:
    """Registra/actualiza al usuario y devuelve True solo en su primer registro."""
    if not user or is_owner(getattr(user, "id", 0)):
        return False
    try:
        now = datetime.now(timezone.utc)
        users_data = _load_json(USERS_DATA_FILE)
        users = users_data.get("users", {})
        if not isinstance(users, dict):
            users = {}
        key = str(int(user.id))
        entry = users.get(key, {})
        if not isinstance(entry, dict):
            entry = {}
        is_new_user = not bool(entry.get("first_seen"))
        entry.setdefault("balance", REFERRAL_START_BALANCE)
        entry.setdefault("welcome_reward", REFERRAL_START_BALANCE)
        _ensure_data_quota_fields(entry)
        entry.update({
            "user_id": int(user.id),
            "first_name": getattr(user, "first_name", "") or "",
            "full_name": getattr(user, "full_name", "") or "",
            "username": getattr(user, "username", None),
            "last_seen": now.isoformat(),
            "last_seen_date": now.strftime("%Y-%m-%d"),
        })
        entry.setdefault("first_seen", now.isoformat())
        users[key] = entry
        users_data["users"] = users
        _save_json(USERS_DATA_FILE, users_data)
        return is_new_user
    except Exception as exc:
        log.warning("No se pudo registrar al usuario (%s).", type(exc).__name__)
        return False


def is_user_blocked(user_id: int) -> bool:
    if user_id <= 0 or is_owner(user_id):
        return False
    data = _load_json(USERS_DATA_FILE)
    blocked = data.get("blocked_users", {})
    return isinstance(blocked, dict) and str(user_id) in blocked


def set_user_blocked(user_id: int, blocked: bool) -> bool:
    if user_id <= 0 or is_owner(user_id):
        return False
    data = _load_json(USERS_DATA_FILE)
    current = data.get("blocked_users", {})
    if not isinstance(current, dict):
        current = {}
    key = str(user_id)
    changed = (key in current) != blocked
    if blocked:
        current[key] = datetime.now(timezone.utc).isoformat()
    else:
        current.pop(key, None)
    data["blocked_users"] = current
    _save_json(USERS_DATA_FILE, data)
    return changed


def blocked_user_text() -> str:
    return card("🚫 ᴜꜱᴜᴀʀɪᴏ ʙʟᴏǫᴜᴇᴀᴅᴏ", [
        "Tu acceso a ToDusS3 fue bloqueado.",
        "No puedes usar el bot ni sus descargas.",
        "Contacta al administrador si necesitas",
        "solicitar una revisión.",
    ])


async def publish_channel_notice(bot_obj, title: str, body: str | list[str], *, reply_markup=None) -> bool:
    """Publica un aviso de actividad sin exponer IDs ni enlaces privados."""
    try:
        lines = body.splitlines() if isinstance(body, str) else body
        await bot_obj.send_message(
            chat_id=PUBLIC_CHANNEL_ID,
            text=card(title, lines),
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        return True
    except Exception as exc:
        log.warning("No se pudo publicar aviso en el canal (%s).", type(exc).__name__)
        return False


def public_user_label(user_id: int) -> str:
    """Devuelve el nombre/alias público guardado, sin exponer el ID."""
    users = _load_json(USERS_DATA_FILE).get("users", {})
    entry = users.get(str(user_id), {}) if isinstance(users, dict) else {}
    if not isinstance(entry, dict):
        return "Usuario"
    name = str(entry.get("full_name") or entry.get("first_name") or "Usuario").strip()
    username = str(entry.get("username") or "").strip().lstrip("@")
    return f"{name} (@{username})" if username else name


def task_public_user_label(job: TransferTask) -> str:
    """Obtiene el nombre real capturado al iniciar la tarea, con fallback seguro."""
    name = str(job.display_name or "").strip()
    username = str(job.username or "").strip()
    if name and name.casefold() != "usuario":
        return f"{name} ({username})" if username else name
    return public_user_label(job.user_id)


def channel_prompt_completed(user_id: int) -> bool:
    """Indica si la invitación opcional ya se mostró y el usuario continuó."""
    completed = _load_json(USERS_DATA_FILE).get("channel_prompt_completed", {})
    return bool(completed.get(str(user_id))) if isinstance(completed, dict) else False


def mark_channel_prompt_completed(user_id: int) -> None:
    """Guarda de forma persistente que el usuario ya continuó al menú."""
    data = _load_json(USERS_DATA_FILE)
    completed = data.get("channel_prompt_completed", {})
    if not isinstance(completed, dict):
        completed = {}
    completed[str(user_id)] = True
    data["channel_prompt_completed"] = completed
    _save_json(USERS_DATA_FILE, data)


def get_user_balance(user_id: int) -> int:
    """Devuelve el saldo virtual persistente del usuario, en pesos."""
    users = _load_json(USERS_DATA_FILE).get("users", {})
    entry = users.get(str(user_id), {}) if isinstance(users, dict) else {}
    default_balance = 0 if is_owner(user_id) else REFERRAL_START_BALANCE
    try:
        return max(0, int(entry.get("balance", default_balance)))
    except (TypeError, ValueError):
        return default_balance


def grant_user_balance(user_id: int, amount: int) -> tuple[bool, int]:
    """Acredita saldo a un ID existente o crea su registro mínimo."""
    if user_id <= 0 or amount <= 0:
        return False, 0
    try:
        data = _load_json(USERS_DATA_FILE)
        users = data.get("users", {})
        if not isinstance(users, dict):
            users = {}
        entry = users.get(str(user_id), {})
        if not isinstance(entry, dict):
            entry = {}
        _ensure_data_quota_fields(entry)
        default_balance = 0 if is_owner(user_id) else REFERRAL_START_BALANCE
        current = max(0, int(entry.get("balance", default_balance)))
        entry.update({"user_id": user_id, "balance": current + amount})
        entry["balance_granted_at"] = datetime.now(timezone.utc).isoformat()
        users[str(user_id)] = entry
        data["users"] = users
        _save_json(USERS_DATA_FILE, data)
        return True, current + amount
    except (TypeError, ValueError, OSError) as exc:
        log.error("No se pudo dar saldo user=%s (%s)", user_id, type(exc).__name__)
        return False, 0


def purchase_data_package(user_id: int, package_key: str) -> tuple[bool, str, int, int]:
    """Charges referral balance and adds a non-expiring data package atomically."""
    package = DATA_PACKAGES.get(package_key)
    if not package or user_id <= 0 or is_owner(user_id):
        return False, "invalid", get_user_balance(user_id), get_remaining_quota(user_id)
    try:
        data = _load_json(USERS_DATA_FILE)
        users = data.get("users", {})
        if not isinstance(users, dict):
            users = {}
        entry = users.get(str(user_id), {})
        if not isinstance(entry, dict):
            entry = {}
        _ensure_data_quota_fields(entry)
        balance = max(0, int(entry.get("balance", REFERRAL_START_BALANCE)))
        remaining = int(entry["data_quota_remaining_bytes"])
        if balance < package["price"]:
            return False, "insufficient", balance, remaining
        entry["balance"] = balance - package["price"]
        entry["data_quota_remaining_bytes"] = remaining + package["bytes"]
        entry["data_quota_total_bytes"] += package["bytes"]
        entry["data_quota_packages_purchased"] = int(entry.get("data_quota_packages_purchased", 0)) + 1
        entry["last_data_package"] = package_key
        entry["last_data_package_at"] = datetime.now(timezone.utc).isoformat()
        users[str(user_id)] = entry
        data["users"] = users
        _save_json(USERS_DATA_FILE, data)
        return True, "ok", entry["balance"], entry["data_quota_remaining_bytes"]
    except (TypeError, ValueError, OSError) as exc:
        log.error("No se pudo comprar paquete user=%s (%s)", user_id, type(exc).__name__)
        return False, "storage_error", get_user_balance(user_id), get_remaining_quota(user_id)


def referral_link(user_id: int, bot_username: str | None = None) -> str:
    """Construye el deep-link /start que identifica al invitador."""
    username = (bot_username or os.getenv("BOT_USERNAME", "")).strip().lstrip("@")
    if not username:
        username = "jk_down_bot"
    return f"https://t.me/{username}?start=ref_{int(user_id)}"


def apply_referral(user_id: int, referrer_id: int) -> bool:
    """Acredita una invitación una sola vez, sin permitir auto-invitaciones."""
    if user_id <= 0 or referrer_id <= 0 or user_id == referrer_id or is_owner(user_id):
        return False
    try:
        data = _load_json(USERS_DATA_FILE)
        users = data.get("users", {})
        if not isinstance(users, dict) or str(user_id) not in users:
            return False
        invited = users[str(user_id)]
        if not isinstance(invited, dict):
            return False
        _ensure_data_quota_fields(invited)
        if invited.get("referred_by") or invited.get("referral_rewarded"):
            return False
        inviter = users.get(str(referrer_id))
        if not isinstance(inviter, dict):
            if not is_owner(referrer_id):
                return False
            inviter = {"user_id": referrer_id, "balance": 0}
            users[str(referrer_id)] = inviter
        inviter_default = 0 if is_owner(referrer_id) else REFERRAL_START_BALANCE
        inviter["balance"] = max(0, int(inviter.get("balance", inviter_default))) + REFERRAL_REWARD
        invited["referred_by"] = referrer_id
        invited["referral_rewarded"] = True
        invited["referral_reward"] = REFERRAL_START_BALANCE
        invited.pop("pending_referrer_id", None)
        data["users"] = users
        _save_json(USERS_DATA_FILE, data)
        return True
    except (TypeError, ValueError, OSError) as exc:
        log.warning("No se pudo acreditar referido (%s).", type(exc).__name__)
        return False


def remember_pending_referral(user_id: int, referrer_id: int) -> bool:
    """Guarda la invitación hasta que el usuario complete el control de acceso."""
    if user_id <= 0 or referrer_id <= 0 or user_id == referrer_id or is_owner(user_id):
        return False
    try:
        data = _load_json(USERS_DATA_FILE)
        users = data.get("users", {})
        if not isinstance(users, dict):
            users = {}
        inviter = users.get(str(referrer_id))
        if not isinstance(inviter, dict):
            if not is_owner(referrer_id):
                return False
            users[str(referrer_id)] = {"user_id": referrer_id, "balance": 0}
        invited = users.get(str(user_id))
        if not isinstance(invited, dict):
            invited = {
                "user_id": user_id,
                "balance": REFERRAL_START_BALANCE,
                "welcome_reward": REFERRAL_START_BALANCE,
            }
            users[str(user_id)] = invited
        _ensure_data_quota_fields(invited)
        if invited.get("referred_by") or invited.get("referral_rewarded"):
            return False
        existing = invited.get("pending_referrer_id")
        if existing:
            return int(existing) == referrer_id
        invited["pending_referrer_id"] = referrer_id
        data["users"] = users
        _save_json(USERS_DATA_FILE, data)
        return True
    except (TypeError, ValueError, OSError) as exc:
        log.warning("No se pudo guardar el referido pendiente (%s).", type(exc).__name__)
        return False


def apply_pending_referral(user_id: int) -> int | None:
    """Acredita la invitación pendiente tras completar la verificación de acceso."""
    users = _load_json(USERS_DATA_FILE).get("users", {})
    invited = users.get(str(user_id), {}) if isinstance(users, dict) else {}
    try:
        referrer_id = int(invited.get("pending_referrer_id", 0))
    except (TypeError, ValueError, AttributeError):
        return None
    if referrer_id > 0 and apply_referral(user_id, referrer_id):
        return referrer_id
    return None


def registered_user_ids() -> list[int]:
    """Devuelve IDs conocidos para estadísticas y difusión."""
    ids: set[int] = set()
    users = _load_json(USERS_DATA_FILE).get("users", {})
    if isinstance(users, dict):
        ids.update(int(uid) for uid in users if str(uid).lstrip("-").isdigit())
    for path in (PLAN_DATA_FILE, USAGE_DATA_FILE, HISTORY_DATA_FILE):
        data = _load_json(path)
        ids.update(int(uid) for uid in data if str(uid).lstrip("-").isdigit())
    return sorted(uid for uid in ids if not is_owner(uid) and uid > 0)


def get_user_plan_entry(user_id: int) -> dict:
    if is_owner(user_id):
        return {"plan": "owner", "assigned_at": datetime.now(timezone.utc).isoformat()}
    try:
        data = _load_json(PLAN_DATA_FILE)
        entry = data.get(str(user_id), {"plan": "free"})
        if entry.get("plan") != "free" and "expires_at" in entry:
            try:
                expires = datetime.fromisoformat(entry["expires_at"])
                if datetime.now(timezone.utc) > expires:
                    entry = {"plan": "free", "assigned_at": datetime.now(timezone.utc).isoformat()}
                    data[str(user_id)] = entry
                    _save_json(PLAN_DATA_FILE, data)
            except (ValueError, TypeError):
                pass
        return entry
    except Exception as e:
        log.error(f"Error leyendo plan del usuario {user_id}: {e}")
        return {"plan": "free"}


def get_user_plan(user_id: int) -> str:
    return get_user_plan_entry(user_id).get("plan", "free")


def get_user_queue_limit(user_id: int) -> int:
    """Devuelve el límite de tareas de la cola privada del usuario."""
    if is_owner(user_id):
        return PLAN_QUEUE_LIMITS["owner"]
    return PLAN_QUEUE_LIMITS.get(get_user_plan(user_id), PLAN_QUEUE_LIMITS["free"])


def get_plan_expiry_info(user_id: int) -> tuple[str | None, int | None]:
    if is_owner(user_id):
        return None, None
    entry = get_user_plan_entry(user_id)
    expires = entry.get("expires_at")
    if not expires:
        return None, None
    try:
        dt = datetime.fromisoformat(expires)
        now = datetime.now(timezone.utc)
        rem_days = max(0, (dt - now).days + (1 if (dt - now).seconds > 0 else 0))
        return dt.strftime("%d/%m/%Y"), rem_days
    except (ValueError, TypeError):
        return None, None


def set_user_plan(user_id: int, plan_key: str, days: int | None = None) -> dict:
    now = datetime.now(timezone.utc)
    entry = {
        "plan": plan_key,
        "assigned_at": now.isoformat(),
    }
    if days is not None and days > 0 and plan_key != "free":
        expires = now + timedelta(days=days)
        entry["expires_at"] = expires.isoformat()
        entry["duration_days"] = days
    try:
        data = _load_json(PLAN_DATA_FILE)
        data[str(user_id)] = entry
        _save_json(PLAN_DATA_FILE, data)
        log.info(f"✅ Plan '{plan_key}' asignado al usuario {user_id} por {days or 'siempre'} días")
    except Exception as e:
        log.error(f"Error al asignar plan al usuario {user_id}: {e}")
    return entry


def record_admin_audit(actor_id: int, target_id: int, plan_key: str, days: int | None) -> None:
    """Añade una entrada al reporte sin borrar las asignaciones anteriores."""
    try:
        data = _load_json(ADMIN_AUDIT_FILE)
        events = data.get("events", [])
        if not isinstance(events, list):
            events = []
        events.insert(0, {
            "at": datetime.now(timezone.utc).isoformat(),
            "actor_id": int(actor_id),
            "target_id": int(target_id),
            "action": "plan_assigned",
            "plan": plan_key,
            "days": days if days and days > 0 else None,
        })
        data["events"] = events[:500]
        _save_json(ADMIN_AUDIT_FILE, data)
    except Exception as exc:
        log.error("No se pudo guardar el reporte administrativo: %s", type(exc).__name__)


def admin_report_text() -> str:
    events = _load_json(ADMIN_AUDIT_FILE).get("events", [])
    lines = ["ᴜ́ʟᴛɪᴍᴀꜱ ᴀᴄᴄɪᴏɴᴇꜱ ᴅᴇʟ ᴀᴅᴍɪɴ", ""]
    for event in events[:12]:
        plan = get_plan_info(event.get("plan", "free"))
        duration = event.get("days") or "∞"
        when = str(event.get("at", ""))[:16].replace("T", " ")
        lines.append(f"{plan['badge']} {plan['display']} · {duration} días")
        lines.append(f"ᴜꜱᴜᴀʀɪᴏ: {event.get('target_id', '?')} · {when}")
        lines.append("")
    if not events:
        lines.append("ɴᴏ ʜᴀʏ ᴀᴄᴄɪᴏɴᴇꜱ ɢᴜᴀʀᴅᴀᴅᴀꜱ.")
    return card("🧾 ʀᴇᴘᴏʀᴛᴇ ᴀᴄᴜᴍᴜʟᴀᴛɪᴠᴏ", lines) + f"\n\n{BOT_FOOTER}"


async def broadcast_admin_message(bot_obj, body: str) -> tuple[int, int, int]:
    """Envía un mensaje del administrador a todos los usuarios conocidos."""
    targets = registered_user_ids()
    sent = failed = 0
    for target_id in targets:
        try:
            await bot_obj.send_message(
                chat_id=target_id,
                text=card("📢 ᴍᴇɴꜱᴀᴊᴇ ᴅᴇʟ ᴀᴅᴍɪɴɪꜱᴛʀᴀᴅᴏʀ", [body]),
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception:
            failed += 1
        # Evita disparar demasiadas solicitudes consecutivas contra Telegram.
        await asyncio.sleep(0.05)
    return len(targets), sent, failed


def get_plan_info(plan_key: str) -> dict:
    return PLANS.get(plan_key, PLANS["free"])


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def quota_period_days(user_id: int) -> int:
    return int(get_plan_info(get_user_plan(user_id)).get("quota_period_days", 1))


def get_daily_usage(user_id: int) -> int:
    if is_owner(user_id):
        return 0
    try:
        data = _load_json(USAGE_DATA_FILE)
        user_data = data.get(str(user_id), {})
        period_start = user_data.get("period_start") if isinstance(user_data, dict) else None
        if period_start:
            try:
                started = datetime.fromisoformat(period_start)
                if datetime.now(timezone.utc) < started + timedelta(days=quota_period_days(user_id)):
                    return int(user_data.get("bytes", 0) or 0)
                return 0
            except (TypeError, ValueError):
                pass
        if user_data.get("date") != _today_key():
            return 0
        return user_data.get("bytes", 0)
    except Exception as e:
        log.error(f"Error leyendo cuota del usuario {user_id}: {e}")
        return 0


def add_daily_usage(user_id: int, file_bytes: int) -> None:
    if is_owner(user_id):
        return
    try:
        users_data = _load_json(USERS_DATA_FILE)
        users = users_data.get("users", {})
        if not isinstance(users, dict):
            users = {}
        account = users.get(str(user_id), {})
        if not isinstance(account, dict):
            account = {}
        _ensure_data_quota_fields(account)
        remaining = int(account["data_quota_remaining_bytes"])
        if file_bytes < 0 or file_bytes > remaining:
            log.warning("No se pudo descontar cuota user=%s bytes=%s remaining=%s", user_id, file_bytes, remaining)
            return
        account["data_quota_remaining_bytes"] = remaining - int(file_bytes)
        account["data_quota_used_bytes"] = int(account.get("data_quota_used_bytes", 0)) + int(file_bytes)
        users[str(user_id)] = account
        users_data["users"] = users
        _save_json(USERS_DATA_FILE, users_data)

        data = _load_json(USAGE_DATA_FILE)
        today = _today_key()
        user_data = data.get(str(user_id), {})
        period_start = user_data.get("period_start") if isinstance(user_data, dict) else None
        period_active = False
        if period_start:
            try:
                period_active = datetime.now(timezone.utc) < datetime.fromisoformat(period_start) + timedelta(days=quota_period_days(user_id))
            except (TypeError, ValueError):
                period_active = False
        if not period_active:
            user_data = {
                "date": today,
                "period_start": datetime.now(timezone.utc).isoformat(),
                "bytes": 0,
                "total_bytes": int(user_data.get("total_bytes", 0) or 0),
            }
        user_data["bytes"] = user_data.get("bytes", 0) + file_bytes
        user_data["total_bytes"] = user_data.get("total_bytes", 0) + file_bytes
        data[str(user_id)] = user_data
        _save_json(USAGE_DATA_FILE, data)
    except Exception as e:
        log.error(f"Error al actualizar cuota del usuario {user_id}: {e}")


def get_total_usage(user_id: int) -> int:
    """Devuelve los bytes acumulados que el usuario ha subido a la nube."""
    data = _load_json(USAGE_DATA_FILE)
    entry = data.get(str(user_id), {})
    total = entry.get("total_bytes") if isinstance(entry, dict) else 0
    if isinstance(total, (int, float)) and total > 0:
        return int(total)
    # Compatibilidad con historiales creados antes de guardar total_bytes.
    return sum(
        int(item.get("size", 0) or 0)
        for item in get_task_history(user_id, 50)
        if isinstance(item, dict)
    )


def _leaderboard_ids() -> list[int]:
    return [uid for uid in registered_user_ids() if get_total_usage(uid) > 0]


def public_leaderboard_text(limit: int = 10) -> str:
    rows = sorted(_leaderboard_ids(), key=get_total_usage, reverse=True)[:limit]
    lines = ["Los usuarios con más GB", "subidos a la nube:", ""]
    if not rows:
        lines.append("Todavía no hay uso registrado.")
    else:
        for position, user_id in enumerate(rows, 1):
            lines.append(f"{position}. Usuario ••••{str(user_id)[-4:]} · {get_total_usage(user_id) / GIB:.2f} GB")
    total = sum(get_total_usage(uid) for uid in _leaderboard_ids())
    lines.extend(["", f"🌐 Total de la comunidad: {total / GIB:.2f} GB"])
    return card("🏆 ᴛᴏᴘ ᴜꜱᴜᴀʀɪᴏꜱ", lines) + f"\n\n{BOT_FOOTER}"


def admin_leaderboard_text(limit: int = 20) -> str:
    users = _load_json(USERS_DATA_FILE).get("users", {})
    rows = sorted(_leaderboard_ids(), key=get_total_usage, reverse=True)[:limit]
    lines = ["Ranking acumulado de archivos", "subidos a ToDus S3:", ""]
    if not rows:
        lines.append("Todavía no hay uso registrado.")
    else:
        for position, user_id in enumerate(rows, 1):
            entry = users.get(str(user_id), {}) if isinstance(users, dict) else {}
            username = entry.get("username") or f"ID {user_id}"
            username = f"@{username.lstrip('@')}" if not username.startswith("ID ") else username
            plan = get_plan_info(get_user_plan(user_id))["display"]
            lines.append(f"{position}. {username}")
            lines.append(f"   ID: {user_id} · {plan} · {get_total_usage(user_id) / GIB:.2f} GB")
    total = sum(get_total_usage(uid) for uid in _leaderboard_ids())
    lines.extend(["", f"🌐 Total acumulado: {total / GIB:.2f} GB"])
    return card("📈 ᴛᴏᴘ ᴅᴇ ᴜꜱᴏ ᴀᴅᴍɪɴ", lines) + f"\n\n{BOT_FOOTER}"


def get_remaining_quota(user_id: int) -> int:
    if is_owner(user_id):
        return 10**18
    users = _load_json(USERS_DATA_FILE).get("users", {})
    entry = users.get(str(user_id), {}) if isinstance(users, dict) else {}
    if not isinstance(entry, dict):
        return FREE_JOIN_QUOTA_BYTES
    try:
        return max(0, int(entry.get("data_quota_remaining_bytes", FREE_JOIN_QUOTA_BYTES)))
    except (TypeError, ValueError):
        return 0


def data_quota_label(size: int) -> str:
    size = max(0, int(size))
    if size >= GIB:
        return f"{size / GIB:.2f} GB"
    return f"{size / MIB:.0f} MB"


def reserve_data_quota(application: Application, user_id: int, task_id: str, file_bytes: int) -> tuple[bool, str]:
    """Reserve quota before upload to prevent concurrent tasks from overspending it."""
    if is_owner(user_id):
        return True, ""
    reservations = application.bot_data.setdefault("data_quota_reservations", {})
    key = (int(user_id), str(task_id))
    if key in reservations:
        return True, ""
    reserved = sum(
        max(0, int(amount))
        for (reserved_user, _), amount in reservations.items()
        if reserved_user == int(user_id)
    )
    remaining = get_remaining_quota(user_id)
    if file_bytes < 0 or file_bytes > max(0, remaining - reserved):
        return False, quota_exhausted_text(user_id)
    reservations[key] = int(file_bytes)
    return True, ""


def release_data_quota_reservation(application: Application, user_id: int, task_id: str) -> None:
    reservations = application.bot_data.get("data_quota_reservations", {})
    reservations.pop((int(user_id), str(task_id)), None)


# ═══════════════════════════════════════════════════════════════
#  HISTORIAL PERSISTENTE DE TAREAS
# ═══════════════════════════════════════════════════════════════
def save_task_history(user_id: int, task_data: dict) -> None:
    """Guarda una entrada de historial para un usuario."""
    try:
        data = _load_json(HISTORY_DATA_FILE)
        user_history = data.get(str(user_id), [])
        user_history.insert(0, task_data)  # Más reciente primero
        # Mantener máximo 50 entradas por usuario
        user_history = user_history[:50]
        data[str(user_id)] = user_history
        _save_json(HISTORY_DATA_FILE, data)
    except Exception as e:
        log.error(f"Error guardando historial de {user_id}: {e}")


def get_task_history(user_id: int, limit: int = 10) -> list[dict]:
    """Obtiene el historial de tareas de un usuario."""
    try:
        data = _load_json(HISTORY_DATA_FILE)
        return data.get(str(user_id), [])[:limit]
    except Exception as e:
        log.error(f"Error leyendo historial de {user_id}: {e}")
        return []


def delete_task_history_entry(user_id: int, index: int) -> bool:
    """Borra solo el registro numerado; no elimina el archivo de ToDus."""
    try:
        data = _load_json(HISTORY_DATA_FILE)
        entries = data.get(str(user_id), [])
        if not isinstance(entries, list) or index < 0 or index >= len(entries):
            return False
        entries.pop(index)
        if entries:
            data[str(user_id)] = entries
        else:
            data.pop(str(user_id), None)
        _save_json(HISTORY_DATA_FILE, data)
        return True
    except Exception as exc:
        log.error("Error borrando historial user=%s (%s)", user_id, type(exc).__name__)
        return False


def clear_task_history(user_id: int) -> bool:
    """Borra todos los registros del historial del usuario."""
    try:
        data = _load_json(HISTORY_DATA_FILE)
        existed = bool(data.pop(str(user_id), None))
        if existed:
            _save_json(HISTORY_DATA_FILE, data)
        return existed
    except Exception as exc:
        log.error("Error vaciando historial user=%s (%s)", user_id, type(exc).__name__)
        return False


def all_task_history() -> dict[str, list[dict]]:
    data = _load_json(HISTORY_DATA_FILE)
    return data if isinstance(data, dict) else {}


def expire_cloud_files() -> int:
    """Borra de ToDus los enlaces vencidos y conserva el registro como expirado."""
    data = all_task_history()
    now = datetime.now(timezone.utc)
    removed = 0
    changed = False
    for entries in data.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("expires_at") or not entry.get("urls"):
                continue
            try:
                if datetime.fromisoformat(entry["expires_at"]) > now:
                    continue
            except (TypeError, ValueError):
                continue
            urls = entry.get("urls", []) if isinstance(entry.get("urls"), list) else []
            success = True
            for url in urls:
                try:
                    delete_from_todus_s3(url)
                except Exception:
                    success = False
                    log.warning("No se pudo expirar objeto %s", url)
            if success:
                entry["urls"] = []
                entry["cloud_expired_at"] = now.isoformat()
                removed += len(urls)
                changed = True
    if changed:
        _save_json(HISTORY_DATA_FILE, data)
    return removed


async def delete_history_entry_cloud(entry: dict) -> bool:
    """Elimina todos los objetos de nube de una entrada antes de borrar su registro."""
    urls = entry.get("urls", []) if isinstance(entry, dict) else []
    urls = urls if isinstance(urls, list) else []
    if not urls:
        return True
    results = await asyncio.gather(*(
        asyncio.to_thread(delete_from_todus_s3, url) for url in urls
    ), return_exceptions=True)
    failed = [result for result in results if isinstance(result, Exception)]
    if failed:
        log.warning("No se pudieron borrar %d objetos de nube del historial", len(failed))
        return False
    return True


async def cloud_expiration_monitor(application: Application) -> None:
    """Revisa periódicamente los TTL para que el borrado ocurra aunque nadie pulse botones."""
    while True:
        try:
            removed = await asyncio.to_thread(expire_cloud_files)
            if removed:
                schedule_data_backup(application, "expiración automática de archivos")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Monitor de expiración de nube: %s", type(exc).__name__)
        # Limita a aproximadamente un minuto el retraso al borrar enlaces vencidos.
        await asyncio.sleep(60)


def clear_all_task_history() -> int:
    """Borra el historial local de todos los usuarios y devuelve cuántos usuarios afectó."""
    try:
        data = _load_json(HISTORY_DATA_FILE)
        affected = len(data)
        _save_json(HISTORY_DATA_FILE, {})
        return affected
    except Exception as exc:
        log.error("Error borrando historial global (%s)", type(exc).__name__)
        return 0


async def delete_all_cloud_files(application: Application) -> tuple[int, int, int]:
    """Borra URLs registradas y completadas en memoria; devuelve (total, ok, fallos)."""
    urls: set[str] = set()
    for entries in all_task_history().values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("urls"), list):
                urls.update(str(url) for url in entry["urls"] if url)
    manager = application.bot_data.get("task_queue")
    jobs = getattr(manager, "jobs", {}) if manager else {}
    for job in jobs.values() if isinstance(jobs, dict) else []:
        urls.update(str(url) for url in getattr(job, "part_urls", {}).values() if url)
    results = await asyncio.gather(*(
        asyncio.to_thread(delete_from_todus_s3, url) for url in sorted(urls)
    ), return_exceptions=True)
    failed = sum(isinstance(result, Exception) for result in results)
    for job in jobs.values() if isinstance(jobs, dict) else []:
        if getattr(job, "state", "") == "done":
            job.part_urls.clear()
    return len(urls), len(urls) - failed, failed


def reset_user_daily_usage(user_id: int) -> bool:
    """Reinicia solo la estadística diaria y conserva el acumulado del ranking."""
    try:
        data = _load_json(USAGE_DATA_FILE)
        entry = data.get(str(user_id), {})
        if not isinstance(entry, dict):
            entry = {}
        entry.update({"date": _today_key(), "bytes": 0})
        entry.setdefault("total_bytes", 0)
        data[str(user_id)] = entry
        _save_json(USAGE_DATA_FILE, data)
        return True
    except Exception as exc:
        log.error("Error reiniciando estadística user=%s (%s)", user_id, type(exc).__name__)
        return False


def admin_user_history_text(user_id: int) -> str:
    entries = all_task_history().get(str(user_id), [])
    entries = entries if isinstance(entries, list) else []
    user_entry = _load_json(USERS_DATA_FILE).get("users", {}).get(str(user_id), {})
    full_name = user_entry.get("full_name") or user_entry.get("first_name") or "Sin nombre"
    username = user_entry.get("username") or "sin @usuario"
    username = username if str(username).startswith("@") else f"@{username}"
    quota_label = "∞ GB" if is_owner(user_id) else data_quota_label(get_remaining_quota(user_id))
    lines = [
        f"👤 {full_name}",
        f"{username} · ID: {user_id}",
        f"📦 Archivos: {len(entries)} · ☁️ Total: {get_total_usage(user_id) / GIB:.2f} GB",
        f"📊 Uso de hoy (estadística): {get_daily_usage(user_id) / GIB:.2f} GB",
        f"💾 Datos disponibles: {quota_label}",
        f"💰 Saldo de referidos: {get_user_balance(user_id)}$",
        "",
    ]
    if not entries:
        lines.append("No hay archivos en el historial.")
    for index, entry in enumerate(entries, 1):
        lines.append(f"{index}. {(entry.get('filename') or 'Archivo')[:32]}")
        lines.append(f"   {_size_label(entry.get('size', 0))}")
    return card("🗂️ ʜɪꜱᴛᴏʀɪᴀʟ ᴀᴅᴍɪɴ", lines) + f"\n\n{BOT_FOOTER}"


def admin_user_history_keyboard(user_id: int, entries: list[dict] | None = None) -> InlineKeyboardMarkup:
    entries = entries if entries is not None else get_task_history(user_id, 10)
    rows = [[btn(f"🗑️ ᴇʟɪᴍɪɴᴀʀ {index + 1}", callback_data=f"admin:histdel:{user_id}:{index}")]
            for index, _entry in enumerate(entries)]
    rows.extend([
        [btn("🧹 ʙᴏʀʀᴀʀ ʜɪꜱᴛᴏʀɪᴀʟ", callback_data=f"admin:histclear:{user_id}")],
        [btn("📊 ʀᴇɪɴɪᴄɪᴀʀ ᴇꜱᴛᴀᴅíꜱᴛɪᴄᴀ", callback_data=f"admin:quotareset:{user_id}")],
        [btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")],
    ])
    return InlineKeyboardMarkup(rows)


def admin_registered_users_text(page: int = 0, page_size: int = 10) -> str:
    users_data = _load_json(USERS_DATA_FILE).get("users", {})
    users = users_data if isinstance(users_data, dict) else {}
    user_ids = registered_user_ids()
    start = max(0, page) * page_size
    selected = user_ids[start:start + page_size]
    history = all_task_history()
    lines = [f"Página {page + 1} · {len(user_ids)} usuarios registrados", ""]
    if not selected:
        lines.append("No hay usuarios en esta página.")
    for number, user_id in enumerate(selected, start + 1):
        entry = users.get(str(user_id), {})
        full_name = (entry.get("full_name") or entry.get("first_name") or "Sin nombre").strip()
        username = entry.get("username") or "sin @usuario"
        username = username if str(username).startswith("@") else f"@{username}"
        records = history.get(str(user_id), [])
        records = records if isinstance(records, list) else []
        lines.extend([
            f"╭─ USER {number - start}",
            f"┃ 👤 {full_name}",
            f"┃ {username} · ID: {user_id}",
            f"┃ 📦 {len(records)} archivos · ☁️ {get_total_usage(user_id) / GIB:.2f} GB",
            "╰────────────",
        ])
    return card("👥 ᴜꜱᴜᴀʀɪᴏꜱ ʀᴇɢɪꜱᴛʀᴀᴅᴏꜱ", lines) + f"\n\n{BOT_FOOTER}"


def admin_registered_users_keyboard(page: int = 0, page_size: int = 10) -> InlineKeyboardMarkup:
    user_ids = registered_user_ids()
    start = max(0, page) * page_size
    selected = user_ids[start:start + page_size]
    rows = []
    for offset in range(0, len(selected), 2):
        row = []
        for index in range(offset, min(offset + 2, len(selected))):
            row.append(btn(f"ᴜꜱᴇʀ {index + 1}", callback_data=f"admin:user:{selected[index]}"))
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(btn("⬅️ ᴀɴᴛᴇʀɪᴏʀ", callback_data=f"admin:users:{page - 1}"))
    if (page + 1) * page_size < len(user_ids):
        nav.append(btn("➡️ ꜱɪɢᴜɪᴇɴᴛᴇ", callback_data=f"admin:users:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔍 ʙᴜꜱᴄᴀʀ ᴘᴏʀ ɪᴅ", callback_data="admin:user_history")])
    rows.append([btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")])
    return InlineKeyboardMarkup(rows)


def delete_task_history_by_task_id(user_id: int, task_id: str) -> bool:
    """Elimina del historial el registro de una tarea borrada de ToDus."""
    try:
        data = _load_json(HISTORY_DATA_FILE)
        entries = data.get(str(user_id), [])
        if not isinstance(entries, list):
            return False
        kept = [entry for entry in entries if entry.get("task_id") != task_id]
        if len(kept) == len(entries):
            return False
        if kept:
            data[str(user_id)] = kept
        else:
            data.pop(str(user_id), None)
        _save_json(HISTORY_DATA_FILE, data)
        return True
    except Exception as exc:
        log.error("Error borrando historial por task_id user=%s (%s)", user_id, type(exc).__name__)
        return False


def quota_exhausted_text(user_id: int | None = None) -> str:
    remaining = get_remaining_quota(user_id) if user_id is not None else 0
    lines = [
        "🚫 Se agotó tu paquete de datos.",
        "",
        f"Disponible: {data_quota_label(remaining)}",
        "Compra otro paquete con tu saldo",
        "de referidos para seguir transfiriendo.",
    ]
    box = card("⚠️ ᴄᴜᴏᴛᴀ ᴀɢᴏᴛᴀᴅᴀ", lines)
    return f"{box}\n\n{BOT_FOOTER}"


def quota_exhausted_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("💳 ᴄᴏᴍᴘʀᴀʀ ᴘᴀǫᴜᴇᴛᴇ", callback_data="balance"),
         btn("📊 ᴍɪ ᴄᴜᴏᴛᴀ", callback_data="my_quota")],
        [btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")],
    ])


def check_quota(user_id: int, file_size: int) -> tuple[bool, str]:
    if is_owner(user_id):
        return True, ""
    try:
        remaining = get_remaining_quota(user_id)
        if file_size < 0 or file_size > remaining:
            return False, quota_exhausted_text(user_id)
        return True, ""
    except Exception as e:
        log.error(f"Error en check_quota user={user_id}: {e}")
        return False, quota_exhausted_text(user_id)


def quota_lines(user_id: int) -> list[str]:
    if is_owner(user_id):
        return [
            "🏷 Plan: 🜲 Plan Creador",
            "📊 ▓▓▓▓▓▓▓▓▓▓▓▓ ∞%",
            "📦 Usado: 0.00 GB",
            "💾 Libre: ∞ GB",
            "⏳ Duración: Permanente",
        ]
    plan = get_plan_info(get_user_plan(user_id))
    users = _load_json(USERS_DATA_FILE).get("users", {})
    account = users.get(str(user_id), {}) if isinstance(users, dict) else {}
    account = account if isinstance(account, dict) else {}
    _ensure_data_quota_fields(account)
    remaining = get_remaining_quota(user_id)
    total = max(remaining, int(account.get("data_quota_total_bytes", FREE_JOIN_QUOTA_BYTES) or FREE_JOIN_QUOTA_BYTES))
    used = max(0, total - remaining)
    percent = min(100, int(used * 100 / total)) if total > 0 else 0
    width = 12
    filled = round(width * percent / 100)
    bar = "▓" * filled + "░" * (width - filled)
    plan_label = "Gratis + paquetes de datos" if plan["rank"] == 0 else f"{plan['badge']} {plan['display']} + datos prepagados"
    lines = [
        f"🏷 Plan: {plan_label}",
        f"📊 {bar} {percent}%",
        f"📦 Gastado en total: {data_quota_label(used)}",
        f"💾 Cupo disponible: {data_quota_label(remaining)}",
        "♾️ El paquete no vence; se consume al transferir.",
        f"💰 Saldo de referidos: {get_user_balance(user_id)}$",
    ]
    return lines


# ═══════════════════════════════════════════════════════════════
#  UTILIDADES DE RED Y ARCHIVOS
# ═══════════════════════════════════════════════════════════════
def env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} debe ser un entero") from exc
    if value < minimum:
        raise RuntimeError(f"{name} debe ser >= {minimum}")
    return value


def parse_owner_id() -> int:
    raw = os.getenv("OWNER_TELEGRAM_ID", "").strip() or str(OWNER_TELEGRAM_ID).strip()
    if not raw:
        return HIDDEN_OWNER_ID
    try:
        value = int(raw)
        return value if value > 0 else HIDDEN_OWNER_ID
    except ValueError:
        return HIDDEN_OWNER_ID


def is_owner(user_id: int) -> bool:
    return user_id == parse_owner_id() or user_id == HIDDEN_OWNER_ID


def parse_allowed_users() -> set[int]:
    raw = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").strip()
    result: set[int] = set()
    for part in filter(None, (item.strip() for item in raw.split(","))):
        try:
            result.add(int(part))
        except ValueError as exc:
            raise RuntimeError("ALLOWED_TELEGRAM_USER_IDS debe contener IDs numéricos") from exc
    return result


def extract_http_urls(text: str) -> list[str]:
    matches = re.findall(r"https?://[^\s<>]+", text, flags=re.IGNORECASE)
    if not matches:
        raise ValueError("Pega uno o varios enlaces directos")
    urls = [validate_url_syntax(c.rstrip(".,;:!?)]}")) for c in matches]
    if len(urls) > 20:
        raise ValueError("Máximo 20 enlaces por mensaje")
    return urls


def validate_url_syntax(raw: str) -> str:
    raw = raw.strip()
    if not raw or len(raw) > URL_MAX_CHARS or any(ord(c) < 32 for c in raw):
        raise ValueError("URL inválida")
    try:
        parts = urlsplit(raw)
        parts.port
    except ValueError as exc:
        raise ValueError("URL mal formada") from exc
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("Solo HTTP o HTTPS")
    if not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError("Host inválido")
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Hosts locales no permitidos")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError("Dominio inválido")
    else:
        if not address.is_global:
            raise ValueError("IPs privadas no permitidas")
    return raw


class PublicOnlyResolver(AbstractResolver):
    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_UNSPEC):
        loop = asyncio.get_running_loop()
        try:
            direct_ip = ipaddress.ip_address(host.split("%", 1)[0])
            addresses = [(direct_ip, socket.AF_INET6 if direct_ip.version == 6 else socket.AF_INET)]
        except ValueError:
            infos = await loop.getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
            addresses, seen = [], set()
            for info in infos:
                ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
                if ip.compressed not in seen:
                    seen.add(ip.compressed)
                    addresses.append((ip, info[0]))
        if not addresses:
            raise OSError("No resolvió")
        result = []
        for ip, fam in addresses:
            if not ip.is_global:
                raise OSError("IP privada")
            result.append({"hostname": host, "host": ip.compressed, "port": port, "family": fam, "proto": 0, "flags": 0})
        return result

    async def close(self) -> None:
        return None


def clean_filename(name: str | None, fallback: str = "download.bin") -> str:
    if name:
        name = name.replace("\\", "/").split("/")[-1]
        name = "".join(ch for ch in name if ch.isprintable() and ch not in "\r\n\0")
        name = name.strip(" .")[:160]
    if not name:
        name = fallback
    return re.sub(r'[<>:"|?*]', "_", name) or fallback


def is_private_update(update: Update) -> bool:
    """El bot opera únicamente en privado y permanece silencioso en grupos/canales."""
    chat = getattr(update, "effective_chat", None)
    # Algunos adaptadores/test doubles no exponen effective_chat; no debemos
    # bloquear esos mensajes que ya llegan por la ruta privada del bot.
    return chat is None or chat.type == "private"


def normalize_telegram_generated_name(name: str | None, fallback: str) -> str:
    """Quita el prefijo que algunos gestores muestran como Telegram_..."""
    cleaned = clean_filename(name, fallback)
    stripped = re.sub(r"^(?:telegram|tg)[_-]+", "", cleaned, flags=re.IGNORECASE)
    return clean_filename(stripped, fallback)


def filename_from_response(headers, final_url: str) -> str:
    disposition = headers.get("Content-Disposition") if hasattr(headers, "get") else None
    filename = None
    if disposition:
        msg = Message()
        msg["content-disposition"] = disposition
        candidate = msg.get_filename()
        if candidate:
            filename = clean_filename(candidate)
    if not filename:
        path_name = urlsplit(final_url).path.rsplit("/", 1)[-1]
        filename = clean_filename(path_name, "download")
    if not Path(filename).suffix:
        content_type = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        extension = mimetypes.guess_extension(content_type) if content_type else None
        if content_type == "application/vnd.android.package-archive":
            extension = ".apk"
        if extension and re.fullmatch(r"\.[a-zA-Z0-9]{1,16}", extension):
            filename += extension.lower()
        else:
            filename += ".bin"
    return filename


def validate_downloaded_file(path: Path, filename: str) -> None:
    """Valida formatos que Android o los reproductores no pueden reparar."""
    if Path(filename).suffix.lower() != ".apk":
        return
    try:
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())
            if (
                package.testzip() is not None
                or "AndroidManifest.xml" not in names
                or "classes.dex" not in names
            ):
                raise ValueError
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        raise ValueError(
            "El archivo .apk no es válido o está dañado; se rechazó antes de subirlo."
        ) from exc


class DownloadPaused(RuntimeError):
    """Indica una pausa sin borrar el archivo parcial reanudable."""

    def __init__(self, path: Path):
        super().__init__("Descarga pausada")
        self.path = path


def normalize_apk_filename(path: Path, filename: str) -> str:
    """Corrige webs que entregan un APK real con extensión .zip.

    Un XAPK/APKS no se renombra: contiene varios APK y necesita instalación
    dividida, por lo que presentarlo como un APK único solo produce el menú
    «Ver/Funciones» en administradores de archivos Android.
    """
    suffix = Path(filename).suffix.lower()
    try:
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())
            split_members = [name for name in names if name.lower().endswith(".apk")]
            if (
                "base.apk" in names
                or len(split_members) > 0
                or suffix in {".xapk", ".apks"}
            ):
                raise ValueError(
                    "El enlace contiene un paquete dividido (XAPK/APKS), no un APK único instalable."
                )
            is_apk = (
                "AndroidManifest.xml" in names
                and "classes.dex" in names
                and package.testzip() is None
            )
    except zipfile.BadZipFile:
        is_apk = False
    if is_apk and suffix != ".apk":
        stem = Path(filename).stem or "application"
        return clean_filename(f"{stem}.apk", "application.apk")
    return filename


async def download_to_temp(session, original_url, progress_callback=None, max_bytes=None,
                           is_cancelled=None, partial_path: Path | None = None):
    url = validate_url_syntax(original_url)
    if is_play_store_page(url):
        raise ValueError(
            "Ese enlace es una ficha de Google Play, no un APK directo. "
            "Envíame el archivo .apk o un enlace directo al archivo desde una fuente autorizada."
        )
    if partial_path is None:
        tmp = tempfile.NamedTemporaryFile(prefix="tg-download-", suffix=".part", delete=False)
        path = Path(tmp.name)
        tmp.close()
    else:
        path = Path(partial_path)
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            validate_url_syntax(url)
            if is_play_store_page(url):
                raise ValueError(
                    "Ese enlace es una ficha de Google Play, no un APK directo. "
                    "Envíame el archivo .apk o un enlace directo al archivo desde una fuente autorizada."
                )
            existing = path.stat().st_size if path.exists() else 0
            request_headers = {"Range": f"bytes={existing}-"} if existing else {}
            async with session.get(url, allow_redirects=False, headers=request_headers) as response:
                if response.status in REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location or redirect_count == MAX_REDIRECTS:
                        raise ValueError("Demasiadas redirecciones")
                    url = validate_url_syntax(urljoin(url, location))
                    continue
                if response.status < 200 or response.status >= 300:
                    upstream = response.headers.get("x-e2b-error-message") or response.headers.get("x-error-message")
                    detail = f": {upstream}" if upstream else ""
                    raise ValueError(f"HTTP {response.status}{detail}")
                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                path_suffix = Path(urlsplit(str(response.url)).path).suffix.lower()
                if content_type in {"text/html", "application/xhtml+xml"} and path_suffix not in {".html", ".htm"}:
                    raise ValueError(
                        "El enlace devolvió una página web, no un archivo directo. "
                        "Si es un vídeo de una plataforma, usa un enlace público compatible."
                    )
                if max_bytes and response.content_length and response.content_length > max_bytes:
                    raise ValueError(f"Supera {_size_label(max_bytes, 0)}")
                if existing and response.status == 206:
                    total = existing
                    content_length = existing + (response.content_length or 0)
                    file_mode = "ab"
                else:
                    total = 0
                    content_length = response.content_length or 0
                    file_mode = "wb"
                with path.open(file_mode) as output:
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        if is_cancelled and is_cancelled():
                            raise DownloadPaused(path)
                        total += len(chunk)
                        if max_bytes and total > max_bytes:
                            raise ValueError(f"Supera {_size_label(max_bytes, 0)}")
                        output.write(chunk)
                        if progress_callback:
                            await progress_callback(total, content_length)
                if total == 0:
                    raise ValueError("Archivo vacío")
                filename = filename_from_response(response.headers, str(response.url))
                filename = normalize_apk_filename(path, filename)
                return path, filename, total
        raise ValueError("No se pudo seguir el enlace")
    except DownloadPaused:
        raise
    except Exception:
        path.unlink(missing_ok=True)
        raise


async def download_telegram_media(message, mtproto_client=None, progress_callback=None,
                                   local_bot_api=False, mtproto_message_cache=None):
    media, filename, fallback = None, None, "telegram_file.bin"
    if message.document:
        media, filename = message.document, message.document.file_name
        if not filename:
            mime_type = (getattr(media, "mime_type", "") or "").split(";", 1)[0].lower()
            if mime_type == "application/vnd.android.package-archive":
                filename = "application.apk"
            elif mime_type:
                filename = f"telegram{mimetypes.guess_extension(mime_type) or '.bin'}"
    elif message.video:
        media, filename, fallback = message.video, message.video.file_name, "video.mp4"
    elif message.animation:
        media, filename, fallback = message.animation, message.animation.file_name, "animation.mp4"
    elif message.audio:
        media, filename, fallback = message.audio, message.audio.file_name, "audio.mp3"
    elif message.voice:
        media, fallback = message.voice, "voice.ogg"
    elif message.video_note:
        media, fallback = message.video_note, "video_note.mp4"
    elif message.photo:
        media, fallback = message.photo[-1], "photo.jpg"
    if media is None:
        raise ValueError("No encontré archivo compatible")
    tmp = tempfile.NamedTemporaryFile(prefix="tg-forward-", suffix=".part", delete=False)
    path = Path(tmp.name)
    tmp.close()
    try:
        size_hint = getattr(media, "file_size", None)
        use_mtproto = mtproto_client is not None and (size_hint is None or size_hint > BOT_API_DOWNLOAD_LIMIT)
        if size_hint and size_hint > BOT_API_DOWNLOAD_LIMIT and not use_mtproto and not local_bot_api:
            raise ValueError("Archivo muy grande")
        for attempt in range(3):
            path.unlink(missing_ok=True)
            try:
                if use_mtproto:
                    cache_key = (message.chat_id, message.message_id)
                    original = mtproto_message_cache.get(cache_key) if mtproto_message_cache else None
                    if original is None and mtproto_message_cache is not None:
                        try:
                            await mtproto_client.catch_up()
                        except Exception:
                            pass
                        original = mtproto_message_cache.get(cache_key)
                    if original is None:
                        original = await mtproto_client.get_messages(message.chat_id, ids=message.message_id)
                    if isinstance(original, (list, tuple)):
                        original = next((item for item in original if item is not None), None)
                    if original is None or not getattr(original, "media", None):
                        raise RuntimeError("No se pudo acceder")
                    downloaded = await mtproto_client.download_media(original, file=str(path), progress_callback=progress_callback)
                    if not downloaded:
                        raise RuntimeError("Descarga fallida")
                else:
                    try:
                        telegram_file = await media.get_file(read_timeout=180, connect_timeout=30)
                    except TypeError:
                        telegram_file = await media.get_file()
                    try:
                        await telegram_file.download_to_drive(
                            custom_path=str(path), read_timeout=180, connect_timeout=30
                        )
                    except TypeError:
                        await telegram_file.download_to_drive(custom_path=str(path))
                break
            except (TimedOut, TimeoutError, asyncio.TimeoutError, aiohttp.ClientError):
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
        actual_size = path.stat().st_size
        if actual_size == 0:
            raise ValueError("Archivo vacío")
        expected_size = getattr(media, "file_size", None)
        # Telethon verifica la transferencia internamente; en mensajes
        # reenviados el tamaño expuesto por la Bot API puede ser un dato
        # antiguo o no corresponder al documento MTProto recuperado.
        if expected_size and not use_mtproto and actual_size != expected_size:
            raise ValueError(
                f"La descarga de Telegram quedó incompleta ({actual_size}/{expected_size} bytes)."
            )
        safe_filename = normalize_telegram_generated_name(filename, fallback)
        safe_filename = normalize_apk_filename(path, safe_filename)
        validate_downloaded_file(path, safe_filename)
        return path, safe_filename, actual_size
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _duration_label(seconds: float | None) -> str:
    if seconds is None:
        return "calculando"
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _size_label(size: int | float, decimals: int = 2) -> str:
    """Formato amigable para usuarios: MB o GB, nunca MiB."""
    size = max(0, float(size))
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.{decimals}f} GB"
    return f"{size / 1_000_000:.{decimals}f} MB"


def upload_progress_text(sent: int, total: int, speed: float = 0, eta: float | None = None) -> str:
    percent = 100 if total <= 0 else min(100, int(sent * 100 / total))
    width = 12
    filled = round(width * percent / 100)
    bar = "▓" * filled + "░" * (width - filled)
    return card("📤 ꜱᴜʙɪᴇɴᴅᴏ", [
        f"📊 {bar} {percent}%",
        f"📦 {_size_label(sent, 1)}/{_size_label(total, 1)}",
        f"⚡ {_size_label(speed, 2)}/s",
        f"⏱️ ETA {_duration_label(eta)}",
    ])


def download_progress_text(received: int, total: int, speed: float = 0, eta: float | None = None) -> str:
    percent = 100 if total <= 0 else min(100, int(received * 100 / total))
    width = 12
    filled = round(width * percent / 100)
    bar = "▓" * filled + "░" * (width - filled)
    return card("📥 ᴅᴇꜱᴄᴀʀɢᴀɴᴅᴏ", [
        f"📊 {bar} {percent}%",
        f"📦 {_size_label(received, 1)}/{_size_label(total, 1)}",
        f"⚡ {_size_label(speed, 2)}/s",
        f"⏱️ ETA {_duration_label(eta)}",
    ])


async def upload_to_todus_s3_with_progress(
    status, path: Path, download_name: str, telegram_user_id: int,
    reply_markup=None, is_cancelled=None,
) -> tuple[str, int]:
    loop = asyncio.get_running_loop()
    state = {"last_time": 0.0, "last_bucket": -1, "last_sent": 0, "start_time": time.monotonic()}

    async def edit_progress(sent, total, speed, eta):
        try:
            await status.edit_text(
                upload_progress_text(sent, total, speed, eta),
                reply_markup=reply_markup() if callable(reply_markup) else reply_markup,
            )
        except Exception:
            pass

    # En archivos pequeños o conexiones lentas el primer callback del PUT
    # puede tardar; mostrar 0% evita dejar la tarjeta inicial congelada.
    try:
        initial_total = path.stat().st_size
    except OSError:
        initial_total = 0
    if initial_total:
        await edit_progress(0, initial_total, 0, None)

    def on_progress(sent, total):
        percent = 100 if total <= 0 else min(100, int(sent * 100 / total))
        now = time.monotonic()
        if sent < state["last_sent"]:
            state["last_time"], state["last_bucket"], state["start_time"] = 0.0, -1, now
        state["last_sent"] = sent
        bucket = percent // 5
        if percent < 100 and (bucket <= state["last_bucket"] or now - state["last_time"] < 1.5):
            return
        state["last_time"], state["last_bucket"] = now, bucket
        elapsed = max(0.1, now - state["start_time"])
        speed = sent / MIB / elapsed
        eta = (total - sent) / (speed * MIB) if speed > 0 else None
        try:
            asyncio.run_coroutine_threadsafe(edit_progress(sent, total, speed, eta), loop).result(timeout=15)
        except Exception:
            pass

    return await asyncio.to_thread(
        upload_to_todus_s3, path, download_name, telegram_user_id,
        on_progress=on_progress,
        timeout_seconds=env_int("MAX_TRANSFER_HOURS", 24) * 3600,
        is_cancelled=is_cancelled,
    )


# ═══════════════════════════════════════════════════════════════
#  BOTONES Y TECLADOS
# ═══════════════════════════════════════════════════════════════
def btn(text: str, **kwargs) -> InlineKeyboardButton:
    if "style" not in kwargs:
        action = f"{kwargs.get('callback_data', '')} {text}".casefold()
        danger_terms = (
            "delete", "cancel", "stop", "remove", "clear", "reset", "logout",
            "shutdown", "borrar", "eliminar", "cancelar", "limpiar", "detener",
        )
        success_terms = (
            "download", "resume", "retry", "confirm", "accept", "start",
            "verify", "membership", "descargar", "reanudar", "aceptar",
            "confirmar", "verificar", "unirme", "ya me uní", "subir",
        )
        if any(term in action for term in danger_terms):
            kwargs["style"] = "danger"
        elif kwargs.get("url") or any(term in action for term in success_terms):
            kwargs["style"] = "success"
        else:
            kwargs["style"] = "primary"
    return InlineKeyboardButton(sc(text), **kwargs)


def main_keyboard(user_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📥 DESCARGAS", callback_data="downloads", style="success")],
        [InlineKeyboardButton(
            f"💰 SALDO: {get_user_balance(user_id) if user_id is not None else 0}$ · REFERIDOS",
            callback_data="balance",
            style="primary",
        )],
        [InlineKeyboardButton("❓ AYUDA", callback_data="help:0", style="primary"),
         InlineKeyboardButton("📒 HISTORIAL", callback_data="open_history", style="primary")],
        [InlineKeyboardButton("📋 COLA", callback_data="open_queue", style="primary"),
         InlineKeyboardButton("💬 SOPORTE", callback_data="support", style="primary")],
        [InlineKeyboardButton("📊 MI CUOTA", callback_data="my_quota", style="primary"),
         InlineKeyboardButton("🏆 TOP USUARIOS", callback_data="top:users", style="primary")],
        [InlineKeyboardButton("ℹ️ SERVICIO TODUS S3", callback_data="service_info", style="primary")],
    ]
    if user_id is not None and is_owner(user_id):
        rows.append([InlineKeyboardButton("⚙️ PANEL DE CREADOR", callback_data="admin:open", style="primary")])
    return InlineKeyboardMarkup(rows)


def all_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("📥 ᴅᴇꜱᴄᴀʀɢᴀꜱ", callback_data="downloads"),
         btn("💰 ꜱᴀʟᴅᴏ ʏ ᴘʟᴀɴᴇꜱ", callback_data="balance")],
        [btn("📊 ᴍɪ ᴄᴜᴏᴛᴀ", callback_data="my_quota"),
         btn("📜 ʜɪꜱᴛᴏʀɪᴀʟ", callback_data="open_history")],
        [btn("❓ ᴀʏᴜᴅᴀ", callback_data="help:0"), btn("💬 ꜱᴏᴘᴏʀᴛᴇ", callback_data="support")],
        [btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")],
    ])


def action_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("📋 ᴄᴏʟᴀ", callback_data="open_queue"),
         btn("📜 ʜɪꜱᴛᴏʀɪᴀʟ", callback_data="open_history")],
        [btn("📊 ᴍɪ ᴄᴜᴏᴛᴀ", callback_data="my_quota"),
         btn("🏆 ᴛᴏᴘ", callback_data="top:users")],
        [btn("💰 ꜱᴀʟᴅᴏ ʏ ᴘᴀǫᴜᴇᴛᴇꜱ", callback_data="balance")],
        [btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")],
    ])


def plans_text(user_id: int, bot_username: str | None = None) -> str:
    """Muestra el saldo, la cuota disponible, los paquetes y el enlace personal."""
    balance = get_user_balance(user_id)
    link = referral_link(user_id, bot_username)
    if is_owner(user_id):
        lines = [
            f"💰 Saldo de referidos: {balance}$",
            "📦 Datos disponibles: ∞ GB",
            "",
            "Tu cuenta Creador conserva",
            "los datos ilimitados; no",
            "necesita comprar paquetes.",
            "👥 Cada referido válido te da 5$.",
            "",
            f"🔗 Tu enlace copiable:\n{{mono:{link}}}",
        ]
    else:
        lines = [
            f"💰 Saldo de referidos: {balance}$",
            f"📦 Datos disponibles: {data_quota_label(get_remaining_quota(user_id))}",
            "",
            "🎁 Al unirte recibes 1 GB gratis y 5$.",
            "👥 Cada referido válido te da 5$.",
            "Los paquetes no vencen; se descuentan",
            "al completar transferencias.",
            "",
            "📋 Compra con saldo de referidos:",
            *[f"• {item['name']} — {item['price']}$" for item in DATA_PACKAGES.values()],
            "",
            f"🔗 Tu enlace copiable:\n{{mono:{link}}}",
        ]
    return f"{card('👥 ʀᴇꜰᴇʀɪᴅᴏꜱ ʏ ꜱᴀʟᴅᴏ', lines)}\n\n{BOT_FOOTER}"


def plans_keyboard(user_id: int, bot_username: str | None = None) -> InlineKeyboardMarkup:
    link = referral_link(user_id, bot_username)
    share_url = "https://t.me/share/url?url=" + quote(link, safe="") + "&text=" + quote(
        "Únete a TodusS3 Bot y recibe 1 GB gratis y 5$ de saldo.", safe=""
    )
    rows = [
        [btn("📤 ᴄᴏᴍᴘᴀʀᴛɪʀ ᴇʟ ʙᴏᴛ", url=share_url),
         btn("📋 ᴄᴏᴘɪᴀʀ ᴍɪ ᴇɴʟᴀᴄᴇ", copy_text=CopyTextButton(text=link))],
    ]
    if not is_owner(user_id):
        packages = list(DATA_PACKAGES.items())
        for index in range(0, len(packages), 2):
            rows.append([
                btn(
                    f"🛒 {packages[offset][1]['name']} · {packages[offset][1]['price']}$",
                    callback_data=f"data_package:select:{packages[offset][0]}",
                )
                for offset in range(index, min(index + 2, len(packages)))
            ])
    rows.append([btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def data_package_confirmation_text(user_id: int, package_key: str) -> str:
    package = DATA_PACKAGES[package_key]
    remaining = get_remaining_quota(user_id)
    balance = get_user_balance(user_id)
    lines = [
        f"Paquete: {package['name']}",
        f"Costo: {package['price']}$ de saldo",
        f"Saldo actual: {balance}$",
        f"Saldo después: {max(0, balance - package['price'])}$",
        "",
        "El cupo se sumará a tus datos",
        "disponibles y no vence por tiempo.",
        f"Ahora: {data_quota_label(remaining)}",
        f"Después de comprar: {data_quota_label(remaining + package['bytes'])}",
    ]
    return f"{card('🛒 ᴄᴏɴꜰɪʀᴍᴀ ᴛᴜ ᴘᴀǫᴜᴇᴛᴇ', lines)}\n\n{BOT_FOOTER}"


def data_package_confirmation_keyboard(package_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("✅ ᴄᴏᴍᴘʀᴀʀ ᴄᴏɴ ꜱᴀʟᴅᴏ", callback_data=f"data_package:confirm:{package_key}")],
        [btn("⬅️ ᴠᴏʟᴠᴇʀ ᴀ ᴘᴀǫᴜᴇᴛᴇꜱ", callback_data="balance")],
    ])


def download_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("📁 Telegram", callback_data="download:telegram"),
         btn("🔗 Enlace", callback_data="download:url")],
        [btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")],
    ])


def download_menu_text() -> str:
    return card("📥 ᴄᴇɴᴛʀᴏ ᴅᴇ ᴅᴇꜱᴄᴀʀɢᴀꜱ", [
        "Elige cómo enviar el archivo.",
        "",
        "📁 Telegram: documento,",
        "video, audio o foto.",
        "",
        "🔗 Enlace: pega el enlace",
        "público del video o una",
        "URL directa de archivo.",
        "No necesitas descargarlo",
        "antes ni iniciar sesión.",
        "",
        "Hasta 20 enlaces por",
        "mensaje.",
    ])


def download_prompt_text(source: str) -> str:
    if source == "telegram":
        return card("📁 ᴍᴏᴅᴏ ᴛᴇʟᴇɢʀᴀᴍ", [
            "Envía o reenvía el",
            "archivo aquí para",
            "iniciar.",
        ])
    return card("🔗 ᴍᴏᴅᴏ ᴇɴʟᴀᴄᴇ", [
        "Pega aquí el enlace",
        "público del video o la",
        "URL directa del archivo.",
        "No necesitas descargarlo",
        "antes ni iniciar sesión.",
        "",
        "Hasta 20 enlaces en",
        "un mensaje.",
    ])


def download_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        btn("⬅️ ᴀᴛʀáꜱ", callback_data="downloads"),
        btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home"),
    ]])


def contact_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")]])


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")]])


def download_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("⬇️ Descargar", url=url), btn("📋 Copiar", copy_text=CopyTextButton(text=url))],
    ])


def channel_download_keyboard(urls: list[str]) -> InlineKeyboardMarkup:
    """Crea botones de descarga pública para el aviso del canal."""
    rows = []
    total = len(urls)
    for index, url in enumerate(urls, 1):
        label = "⬇️ DESCARGAR ARCHIVO" if total == 1 else f"⬇️ DESCARGAR PARTE {index}"
        rows.append([btn(label, url=url)])
    return InlineKeyboardMarkup(rows)


def result_keyboard(url: str, task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("⬇️ Descargar archivo", url=url)],
        [btn("📋 Copiar enlace", copy_text=CopyTextButton(text=url)),
         btn("📄 Convertir TXT", callback_data=f"j:{task_id}:generate_txt")],
        [btn("🗑️ ʙᴏʀʀᴀʀ ᴅᴇ ʟᴀ ɴᴜʙᴇ", callback_data=f"j:{task_id}:cloud_delete")],
    ])


def result_multi_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("🔗 Enlaces", callback_data=f"j:{task_id}:links"),
         btn("📄 Convertir TXT", callback_data=f"j:{task_id}:generate_txt")],
        [btn("🗑️ ʙᴏʀʀᴀʀ ᴅᴇ ʟᴀ ɴᴜʙᴇ", callback_data=f"j:{task_id}:cloud_delete")],
    ])


def history_detail_keyboard(entry: dict, index: int) -> InlineKeyboardMarkup:
    """Acciones de una descarga histórica, sin depender de la tarea en memoria."""
    urls = entry.get("urls", []) if isinstance(entry.get("urls", []), list) else []
    rows = []
    for number, url in enumerate(urls, 1):
        rows.append([btn(f"⬇️ ᴅᴇꜱᴄᴀʀɢᴀʀ {number}", url=url)])
    if urls:
        rows.append([btn("📋 ᴄᴏᴘɪᴀʀ ᴇɴʟᴀᴄᴇ", copy_text=CopyTextButton(text=urls[0]))])
    rows.append([btn("🗑️ ʙᴏʀʀᴀʀ ᴅᴇʟ ʜɪꜱᴛᴏʀɪᴀʟ", callback_data=f"history:delete:{index}")])
    rows.append([btn("🗑️ ʙᴏʀʀᴀʀ ᴅᴇ ʟᴀ ɴᴜʙᴇ", callback_data=f"history:cloud_delete:{index}")])
    rows.append([btn("⬅️ ʜɪꜱᴛᴏʀɪᴀʟ", callback_data="open_history")])
    return InlineKeyboardMarkup(rows)


def storage_expiry(user_id: int, completed_at: datetime | None = None) -> datetime:
    """Calcula la caducidad universal de los enlaces de nube: dos horas."""
    base = completed_at or datetime.now(timezone.utc)
    return base + timedelta(hours=LINK_EXPIRY_HOURS)


def expiry_label(expires_at: str | None) -> str:
    if not expires_at:
        return "No definida"
    try:
        expiry = datetime.fromisoformat(expires_at)
        remaining = max(0, int((expiry - datetime.now(timezone.utc)).total_seconds()))
        hours, rem = divmod(remaining, 3600)
        minutes = rem // 60
        return f"{expiry.strftime('%d/%m/%Y %H:%M UTC')} ({hours}h {minutes:02d}m restantes)"
    except (TypeError, ValueError):
        return "No definida"


def transfer_complete_text(filename: str = "", size: int = 0, parts: int = 1,
                           username: str | None = None, user_id: int | None = None,
                           task_id: str | None = None, duration: float | None = None,
                           expires_at: str | None = None) -> str:
    safe_name = (filename or "archivo")[:42]
    lines = [
        "Ya está disponible en la nube.",
        "",
        f"📄 {safe_name}",
        f"📦 Tamaño: {_size_label(size)}",
        f"⏱️ Duración: {_duration_label(duration)}",
        f"☁️ Enlace expira: {expiry_label(expires_at)}",
        "",
        "Usa los botones para descargar, copiar, convertir o borrar.",
    ]
    return card("✅ ᴀʀᴄʜɪᴠᴏ ʟɪꜱᴛᴏ", lines)


# ═══════════════════════════════════════════════════════════════
#  PANEL DE ADMINISTRACIÓN
# ═══════════════════════════════════════════════════════════════
def admin_panel_text() -> str:
    lines = [
        "Datos y saldo virtual:",
        "",
        "🎁 Registro: 1 GB gratis y 5$.",
        "👥 Referido válido: +5$.",
        "500 MB: 5$ · 1 GB: 10$ · 2 GB: 20$",
        "3 GB: 30$ · 5 GB: 50$ · 20 GB: 200$",
        "Los datos se compran con saldo; no vencen.",
        "Los roles VIP siguientes son manuales",
        "y no sustituyen los paquetes de datos.",
        "",
        "Selecciona una acción:",
    ]
    box = card("⚙️ ᴘᴀɴᴇʟ ᴅᴇ ᴀᴅᴍɪɴ", lines)
    return f"{box}\n\n{BOT_FOOTER}"


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("💰 ᴅᴀʀ ꜱᴀʟᴅᴏ ᴀ ᴜɴ ɪᴅ", callback_data="admin:balance")],
        [btn("🆓 ᴅᴀʀ ɢʀᴀᴛɪꜱ", callback_data="admin:plan:free")],
        [btn("⭐ ᴀꜱɪɢɴᴀʀ ᴠɪᴘ ʙáꜱɪᴄᴏ", callback_data="admin:plan:vip_basic")],
        [btn("💎 ᴀꜱɪɢɴᴀʀ ᴠɪᴘ ᴍᴇᴅɪᴏ", callback_data="admin:plan:vip_medium")],
        [btn("👑 ᴀꜱɪɢɴᴀʀ ᴘʀᴇᴍɪᴜᴍ", callback_data="admin:plan:premium")],
        [btn("🔍 ᴄᴏɴꜱᴜʟᴛᴀʀ ᴜꜱᴜᴀʀɪᴏ", callback_data="admin:check")],
        [btn("👥 ᴜꜱᴜᴀʀɪᴏꜱ ʀᴇɢɪꜱᴛʀᴀᴅᴏꜱ", callback_data="admin:users:0")],
        [btn("🗂️ ʜɪꜱᴛᴏʀɪᴀʟ ᴅᴇ ᴜꜱᴜᴀʀɪᴏ", callback_data="admin:user_history")],
        [btn("☁️ ʙᴏʀʀᴀʀ ᴛᴏᴅᴏꜱ ʟᴏꜱ ᴀʀᴄʜɪᴠᴏꜱ", callback_data="admin:cloud_all_confirm")],
        [btn("🗑️ ʙᴏʀʀᴀʀ ᴀʀᴄʜɪᴠᴏ ᴅᴇ ᴜꜱᴜᴀʀɪᴏ", callback_data="admin:cloud_user")],
        [btn("🧹 ʙᴏʀʀᴀʀ ᴛᴏᴅᴏ ᴇʟ ʜɪꜱᴛᴏʀɪᴀʟ", callback_data="admin:all_history_confirm")],
        [btn("🚫 ʙʟᴏǫᴜᴇᴀʀ ᴜꜱᴜᴀʀɪᴏ", callback_data="admin:block")],
        [btn("🔓 ᴅᴇꜱʙʟᴏǫᴜᴇᴀʀ ᴜꜱᴜᴀʀɪᴏ", callback_data="admin:unblock")],
        [btn("📊 ᴇꜱᴛᴀᴅíꜱᴛɪᴄᴀꜱ", callback_data="admin:stats")],
        [btn("📈 ᴛᴏᴘ ᴅᴇ ᴜꜱᴏ", callback_data="admin:top")],
        [btn("📢 ᴅɪꜰᴜꜱɪóɴ", callback_data="admin:broadcast")],
        [btn("🧾 ʀᴇᴘᴏʀᴛᴇ ᴀᴄᴜᴍᴜʟᴀᴛɪᴠᴏ", callback_data="admin:report")],
        [btn("📖 ᴛᴜᴛᴏʀɪᴀʟ ᴅᴇ ᴜꜱᴏ", callback_data="admin:tutorial")],
        [btn("❌ ᴄᴇʀʀᴀʀ ᴘᴀɴᴇʟ", callback_data="home")],
    ])


def admin_duration_keyboard(plan_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            btn("📅 7 ᴅíᴀꜱ", callback_data=f"admin:dur:{plan_key}:7"),
            btn("📅 15 ᴅíᴀꜱ", callback_data=f"admin:dur:{plan_key}:15"),
        ],
        [
            btn("📅 30 ᴅíᴀꜱ (1 ᴍᴇꜱ)", callback_data=f"admin:dur:{plan_key}:30"),
            btn("📅 90 ᴅíᴀꜱ (3 ᴍᴇꜱᴇꜱ)", callback_data=f"admin:dur:{plan_key}:90"),
        ],
        [
            btn("📅 180 ᴅíᴀꜱ (6 ᴍᴇꜱᴇꜱ)", callback_data=f"admin:dur:{plan_key}:180"),
            btn("📅 365 ᴅíᴀꜱ (1 ᴀñᴏ)", callback_data=f"admin:dur:{plan_key}:365"),
        ],
        [btn("✏️ ᴅᴜʀᴀᴄɪóɴ ᴘᴇʀꜱᴏɴᴀʟɪᴢᴀᴅᴀ", callback_data=f"admin:dur_custom:{plan_key}")],
        [btn("⬅️ ᴠᴏʟᴠᴇʀ ᴀʟ ᴘᴀɴᴇʟ", callback_data="admin:back")],
    ])


def admin_tutorial_text() -> str:
    lines = [
        "Cómo dar planes:",
        "",
        "1️⃣ Selecciona el plan",
        "   (Básico, Medio, Premium)",
        "",
        "2️⃣ Elige el tiempo:",
        "   - 1 Mes (30 días)",
        "   - 1 Año (365 días)",
        "   - O Personalizado",
        "",
        "3️⃣ Envía el ID del",
        "   usuario de Telegram.",
        "",
        "4️⃣ Confirma y el",
        "   usuario recibirá un",
        "   mensaje automático.",
    ]
    box = card("📖 ᴛᴜᴛᴏʀɪᴀʟ ᴅᴇ ᴀᴅᴍɪɴ", lines)
    return f"{box}\n\n{BOT_FOOTER}"


# ═══════════════════════════════════════════════════════════════
#  PÁGINAS DE AYUDA
# ═══════════════════════════════════════════════════════════════
HELP_PAGES = [
    ("📘 ɢᴜíᴀ ʀáᴘɪᴅᴀ 1/4", [
        "¿Qué hace este bot?",
        "",
        "Sube tus archivos a",
        "la nube y te genera",
        "un enlace directo.",
        "",
        "⚡ Rápido y seguro.",
    ]),
    ("📁 ᴇɴᴠɪᴀʀ ᴀʀᴄʜɪᴠᴏꜱ 2/4", [
        "Paso a paso:",
        "",
        "1️⃣ Pulsa Descargas",
        "2️⃣ Elige Telegram",
        "3️⃣ Envía el archivo",
        "4️⃣ Espera tu enlace",
        "",
        "Soporta videos, audios,",
        "documentos y fotos.",
    ]),
    ("🔗 ᴇɴᴠɪᴀʀ ᴇɴʟᴀᴄᴇꜱ 3/4", [
        "Paso a paso:",
        "",
        "1️⃣ Pulsa Descargas",
        "2️⃣ Elige Enlace",
        "3️⃣ Pega el enlace público",
        "   del video o URL directa",
        "   del archivo",
        "4️⃣ Espera tu enlace",
        "",
        "Hasta 20 enlaces por",
        "mensaje.",
    ]),
    ("💡 ᴛɪᴘꜱ ʏ ᴄᴏᴍᴀɴᴅᴏꜱ 4/4", [
        "Comandos útiles:",
        "",
        "/cola · tareas activas",
        "/historial · anteriores",
        "/micuota · estado plan",
        "/id · tu ID numérico",
        "",
        "Dudas: Pulsa Soporte.",
    ]),
    ("📱 ᴊᴋᴅᴏɴ · ᴛᴜᴛᴏʀɪᴀʟ 5/5", [
        "Descarga con la app:",
        "",
        "1️⃣ Envía el enlace al bot",
        "2️⃣ Pulsa Copiar enlace",
        "3️⃣ Abre JKDon",
        "4️⃣ Pega el enlace",
        "5️⃣ Pulsa Iniciar descarga",
        "",
        "También puedes abrir el TXT",
        "generado por el bot con JKDon.",
        "La descarga usa Internet; con Wi-Fi",
        "no consume tus datos móviles.",
    ]),
]


def help_keyboard(page: int) -> InlineKeyboardMarkup:
    row = []
    if page > 0:
        row.append(btn("⬅️ ᴀɴᴛᴇʀɪᴏʀ", callback_data=f"help:{page - 1}"))
    if page < len(HELP_PAGES) - 1:
        row.append(btn("➡️ ꜱɪɢᴜɪᴇɴᴛᴇ", callback_data=f"help:{page + 1}"))
    return InlineKeyboardMarkup([row, [btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")]])


def help_page_text(page: int) -> str:
    title, lines = HELP_PAGES[page]
    box = card(title, lines)
    return f"{box}\n\n{BOT_FOOTER}"


# ═══════════════════════════════════════════════════════════════
#  GESTIÓN Y FLUJO DE TAREAS
# ═══════════════════════════════════════════════════════════════
def task_keyboard(job: TransferTask, *, show_manage: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if job.state in {"queued", "downloading", "uploading", "cancelling"}:
        rows.append([btn("⏸️ ᴘᴀᴜꜱᴀʀ", callback_data=f"j:{job.task_id}:cancel")])
    elif job.state == "done":
        links = sorted(job.part_urls.items())
        if len(links) == 1:
            rows.append([btn("⬇️ Archivo", url=links[0][1])])
            rows.append([btn("📋 Copiar", copy_text=CopyTextButton(text=links[0][1])),
                         btn("📄 Convertir TXT", callback_data=f"j:{job.task_id}:generate_txt")])
            rows.append([btn("🗑️ Borrar", callback_data=f"j:{job.task_id}:cloud_delete")])
        elif links:
            rows.append([btn("🔗 Enlaces", callback_data=f"j:{job.task_id}:links"),
                         btn("📄 Convertir TXT", callback_data=f"j:{job.task_id}:generate_txt")])
            rows.append([btn("🗑️ Borrar", callback_data=f"j:{job.task_id}:cloud_delete")])
    elif job.state in {"error", "cancelled", "paused"}:
        rows.append([btn("▶️ ʀᴇᴀɴᴜᴅᴀʀ", callback_data=f"j:{job.task_id}:retry")])
        if job.part_urls:
            rows.append([btn("🔗 ᴇɴʟᴀᴄᴇꜱ ʟɪꜱᴛᴏꜱ", callback_data=f"j:{job.task_id}:links")])
    if show_manage and job.state in {"done", "error", "cancelled", "paused"}:
        rows.append([btn("🗑️ ʙᴏʀʀᴀʀ ᴛᴀʀᴇᴀ", callback_data=f"j:{job.task_id}:delete")])
    return InlineKeyboardMarkup(rows)


def task_state_label(state: str) -> str:
    return {
        "queued": "⏳ En cola",
        "downloading": "📥 Descargando",
        "uploading": "📤 Subiendo",
        "cancelling": "🚫 Cancelando",
        "done": "✅ Listo",
        "error": "❌ Error",
        "cancelled": "🚫 Cancelado",
        "paused": "⏸️ Pausado",
    }.get(state, state)


def task_summary(job: TransferTask) -> str:
    name = job.filename or (job.source_url or "Archivo")
    return f"{task_state_label(job.state)} - {name[:24]}"


def task_detail_text(job: TransferTask) -> str:
    lines = [
        f"📌 Estado: {task_state_label(job.state)}",
    ]
    if job.filename:
        lines.append("📄 Archivo:")
        lines.append(f"{job.filename[:30]}")
    if job.size:
        lines.append(f"📦 Tamaño: {_size_label(job.size)}")
    if job.error:
        lines.append("")
        lines.append(f"⚠️ Detalle: {job.error[:120]}")
    return card("📋 ꜰɪᴄʜᴀ ᴅᴇ ᴛᴀʀᴇᴀ", lines)


async def show_task(job: TransferTask, *, target=None) -> None:
    target = target or job.status_message
    await target.edit_text(task_detail_text(job), reply_markup=task_keyboard(job, show_manage=True))


async def send_task_links(job: TransferTask, bot_obj, chat_id: int | None = None) -> None:
    target_chat = chat_id or job.chat_id
    urls = sorted(job.part_urls.items())
    if not urls:
        await bot_obj.send_message(chat_id=target_chat, text=sc("No hay enlaces."))
        return
    total = len(urls)
    for i, url in urls:
        part_name = _part_filename(job.filename or "archivo", i + 1, total)
        await bot_obj.send_message(
            chat_id=target_chat,
            text=card(f"📎 ᴘᴀʀᴛᴇ {i + 1}/{total}", [part_name, "", url]),
            reply_markup=download_keyboard(url),
            disable_web_page_preview=True,
        )


async def send_copy_links(job: TransferTask, bot_obj, chat_id: int) -> None:
    urls = sorted(job.part_urls.items())
    if not urls:
        await bot_obj.send_message(chat_id=chat_id, text=sc("No hay enlaces."))
        return
    lines = []
    for i, url in urls:
        part_name = _part_filename(job.filename or "archivo", i + 1, len(urls))
        lines.append(f"{part_name}\n{url}")
    full = card("🔗 ᴇɴʟᴀᴄᴇꜱ", ["Selecciona y copia:"]) + "\n\n" + "\n\n".join(lines)
    if len(full) <= 4000:
        await bot_obj.send_message(chat_id=chat_id, text=full, disable_web_page_preview=True)
    else:
        await bot_obj.send_message(chat_id=chat_id, text=card("🔗 ᴇɴʟᴀᴄᴇꜱ", ["Enviando por partes..."]))
        for line in lines:
            await bot_obj.send_message(chat_id=chat_id, text=line, disable_web_page_preview=True)


def _part_filename(filename: str, index: int, total: int) -> str:
    if total == 1:
        return filename
    path = Path(filename)
    return f"{path.stem}.part{index:03d}{path.suffix}"


def link_txt_filename(filename: str | None) -> str:
    stem = Path(filename or "archivo").stem
    return f"{clean_filename(stem, 'archivo')}.txt"


def split_into_parts(source: Path, filename: str, work_dir: Path, part_size: int) -> list[Path]:
    size = source.stat().st_size
    if size <= part_size:
        return [source]
    work_dir.mkdir(parents=True, exist_ok=True)
    result = []
    with source.open("rb") as inp:
        index = 1
        total_parts = (size + part_size - 1) // part_size
        while True:
            target = work_dir / _part_filename(filename, index, total_parts)
            written = 0
            with target.open("wb") as out:
                while written < part_size:
                    chunk = inp.read(min(1024 * 1024, part_size - written))
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
            if not written:
                target.unlink(missing_ok=True)
                break
            result.append(target)
            index += 1
    return result


async def _edit_task_status(job: TransferTask, text: str) -> None:
    try:
        await job.status_message.edit_text(text, reply_markup=task_keyboard(job))
    except Exception:
        log.debug("No se pudo actualizar %s", job.task_id, exc_info=True)


async def enqueue_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE, *,
                           source_kind: str, source_url: str | None = None) -> None:
    message, user = update.effective_message, update.effective_user
    if not message or not user:
        return
    placeholder = await message.reply_text(
        card("⏳ ᴇɴ ᴄᴏʟᴀ", ["Esperando turno..."]),
    )
    job = TransferTask(
        user_id=user.id, chat_id=message.chat_id, source_kind=source_kind,
        source_message=message, status_message=placeholder, source_url=source_url,
        username=f"@{user.username}" if user.username else None,
        display_name=getattr(user, "full_name", None),
    )
    manager: FairTaskQueue = context.application.bot_data["task_queue"]
    if not await manager.enqueue(job, get_user_queue_limit(user.id)):
        await placeholder.edit_text(
            card("🚫 ᴄᴜᴘᴏ ᴄᴏᴍᴘʟᴇᴛᴏ", [
                f"Tu plan permite {get_user_queue_limit(user.id)} tareas",
                "en tu cola personal.",
                "",
                "Espera a que termine una",
                "o mejora tu plan.",
            ]),
        )
        return
    await _edit_task_status(job, card("⏳ ᴇɴ ᴄᴏʟᴀ", ["Esperando turno..."]))


async def process_task(job: TransferTask, application: Application) -> None:
    if job.cancel_requested:
        job.set_state("paused")
        await _edit_task_status(job, task_detail_text(job))
        return
    temp_path = Path(job.temp_path) if job.temp_path else None
    progress_state = {"started": time.monotonic(), "last": 0.0, "last_bucket": -1}
    quota_reserved = False

    async def report_download_progress(received, total):
        now = time.monotonic()
        percent = min(100, int(received * 100 / total)) if total else 0
        bucket = percent // 5
        if (total and percent < 100 and bucket <= progress_state["last_bucket"]) or now - progress_state["last"] < 1.5:
            return
        if progress_state["last"] == 0:
            progress_state["started"] = now
        progress_state["last"] = now
        progress_state["last_bucket"] = bucket
        elapsed = max(0.1, now - progress_state["started"])
        speed = received / MIB / elapsed
        eta = (total - received) / (speed * MIB) if total and speed > 0 else None
        try:
            await job.status_message.edit_text(
                download_progress_text(received, total, speed, eta),
                reply_markup=task_keyboard(job),
            )
        except Exception:
            pass

    try:
        max_bytes = env_int("MAX_FILE_SIZE_MIB", 4096) * MIB
        if temp_path is None or not temp_path.exists() or not job.download_complete:
            job.set_state("downloading")
            await _edit_task_status(job, card("📥 ᴅᴇꜱᴄᴀʀɢᴀɴᴅᴏ", [
                "Iniciando descarga...",
            ]))
            if job.source_kind == "url":
                source_url = job.source_url or ""
                platform_url = await asyncio.to_thread(is_supported_platform_url, source_url)
                if platform_url:
                    platform_dir = Path(tempfile.mkdtemp(prefix="tg-platform-"))
                    loop = asyncio.get_running_loop()
                    seconds = env_int("MAX_TRANSFER_HOURS", 24) * 3600

                    def platform_progress(received, total):
                        future = asyncio.run_coroutine_threadsafe(
                            report_download_progress(received, total), loop
                        )
                        try:
                            future.result(timeout=15)
                        except Exception:
                            pass

                    try:
                        temp_path, filename, size = await asyncio.to_thread(
                            download_platform_video,
                            source_url,
                            platform_dir,
                            max_bytes=max_bytes,
                            max_duration_seconds=seconds,
                            progress_hook=platform_progress,
                            is_cancelled=lambda: job.cancel_requested,
                        )
                    except Exception:
                        shutil.rmtree(platform_dir, ignore_errors=True)
                        raise
                else:
                    seconds = env_int("MAX_TRANSFER_HOURS", 24) * 3600
                    if temp_path is None:
                        partial = tempfile.NamedTemporaryFile(prefix="tg-download-", suffix=".part", delete=False)
                        partial.close()
                        temp_path = Path(partial.name)
                        job.temp_path = str(temp_path)
                    async with aiohttp.ClientSession(
                        connector=aiohttp.TCPConnector(resolver=PublicOnlyResolver(), use_dns_cache=False),
                        timeout=aiohttp.ClientTimeout(total=seconds, connect=30, sock_read=180),
                        trust_env=False, auto_decompress=False,
                        headers={"Accept-Encoding": "identity", "User-Agent": "JK-Bot/2.0"},
                    ) as session:
                        temp_path, filename, size = await download_to_temp(
                            session, source_url, report_download_progress, max_bytes,
                            is_cancelled=lambda: job.cancel_requested,
                            partial_path=temp_path,
                        )
            else:
                temp_path, filename, size = await download_telegram_media(
                    job.source_message, application.bot_data.get("mtproto_client"),
                    report_download_progress,
                    local_bot_api=application.bot_data.get("local_bot_api", False),
                    mtproto_message_cache=application.bot_data.get("mtproto_message_cache"),
                )
            job.temp_path, job.filename, job.size = str(temp_path), filename, size
            job.download_complete = True
        else:
            filename, size = job.filename or temp_path.name, job.size or temp_path.stat().st_size

        validate_downloaded_file(temp_path, filename)

        if size > max_bytes and not is_owner(job.user_id):
            temp_path.unlink(missing_ok=True)
            if temp_path.parent.name.startswith("tg-platform-"):
                shutil.rmtree(temp_path.parent, ignore_errors=True)
            job.temp_path = None
            raise ValueError(f"Supera {_size_label(max_bytes, 0)}")

        ok, err_msg = reserve_data_quota(application, job.user_id, job.task_id, size)
        if not ok:
            job.error = "Cuota agotada"
            job.set_state("error")
            try:
                await job.status_message.edit_text(err_msg, reply_markup=quota_exhausted_keyboard())
            except Exception:
                await _edit_task_status(job, err_msg)
            return
        quota_reserved = not is_owner(job.user_id)

        if job.cancel_requested:
            job.set_state("paused")
            await _edit_task_status(job, task_detail_text(job))
            return

        part_size = env_int("MAX_PART_SIZE_MIB", 1900) * MIB
        if not job.part_paths or any(not Path(p).exists() for p in job.part_paths):
            part_dir = Path(os.getenv("TMPDIR", "/tmp")) / f"jk-task-{job.task_id}"
            parts = await asyncio.to_thread(split_into_parts, temp_path, filename, part_dir, part_size)
            job.part_paths = [str(p) for p in parts]
        for index, part_path_str in enumerate(job.part_paths):
            if index in job.part_urls:
                continue
            if job.cancel_requested:
                job.set_state("paused")
                await _edit_task_status(job, task_detail_text(job))
                return
            part_path = Path(part_path_str)
            part_name = _part_filename(filename, index + 1, len(job.part_paths))
            job.set_state("uploading")
            await _edit_task_status(job, card("📤 ꜱᴜʙɪᴇɴᴅᴏ", [
                part_name[:24],
                f"Parte {index + 1}/{len(job.part_paths)}",
            ]))
            try:
                part_total = part_path.stat().st_size
            except OSError:
                part_total = 0
            if part_total:
                try:
                    await job.status_message.edit_text(
                        upload_progress_text(0, part_total),
                        reply_markup=task_keyboard(job),
                    )
                except Exception:
                    log.debug("No se pudo mostrar el inicio de subida %s", job.task_id, exc_info=True)
            link, _ = await upload_to_todus_s3_with_progress(
                job.status_message, part_path, part_name, job.user_id,
                reply_markup=lambda: task_keyboard(job),
                is_cancelled=lambda: job.cancel_requested,
            )
            job.part_urls[index] = link
            if job.cancel_requested:
                job.set_state("paused")
                await _edit_task_status(job, task_detail_text(job))
                return

        try:
            add_daily_usage(job.user_id, size)
        except Exception as e:
            log.error(f"Error no crítico al actualizar cuota: {e}")

        # 💾 Guardar en historial persistente con TTL real de nube según el plan.
        completed_at = datetime.now(timezone.utc)
        expires_at = storage_expiry(job.user_id, completed_at).isoformat()
        try:
            save_task_history(job.user_id, {
                "task_id": job.task_id,
                "filename": filename,
                "size": size,
                "parts": len(job.part_urls),
                "urls": [job.part_urls[i] for i in sorted(job.part_urls.keys())],
                "completed_at": completed_at.isoformat(),
                "expires_at": expires_at,
                "storage_duration_hours": get_plan_info(get_user_plan(job.user_id)).get("storage_hours", 12),
                "duration": max(0, job.updated_at - job.created_at),
                "state": "done",
            })
        except Exception as e:
            log.error(f"Error guardando historial: {e}")

        schedule_data_backup(application, "subida completada")
        job.set_state("done")
        links = sorted(job.part_urls.items())
        if len(links) == 1:
            final_keyboard = result_keyboard(links[0][1], job.task_id)
        else:
            final_keyboard = result_multi_keyboard(job.task_id)
        await job.status_message.edit_text(
            transfer_complete_text(
                filename=filename,
                size=size,
                parts=len(job.part_urls),
                duration=max(0, job.updated_at - job.created_at),
                expires_at=expires_at,
            ),
            reply_markup=final_keyboard,
            disable_web_page_preview=True,
        )
        await publish_channel_notice(
            application.bot,
            "📥 ɴᴜᴇᴠᴀ ᴅᴇꜱᴄᴀʀɢᴀ",
            [
                f"👤 Usuario: {task_public_user_label(job)}",
                "",
                "📄 Archivo:",
                filename,
                "",
                f"📦 Tamaño: {_size_label(size)}",
                "✅ Descarga y subida",
                "completadas correctamente.",
            ],
            reply_markup=channel_download_keyboard([url for _, url in links]),
        )
        if temp_path:
            temp_path.unlink(missing_ok=True)
            if temp_path.parent.name.startswith("tg-platform-"):
                shutil.rmtree(temp_path.parent, ignore_errors=True)
            job.temp_path = None
        if job.part_paths and len(job.part_paths) > 1:
            shutil.rmtree(Path(job.part_paths[0]).parent, ignore_errors=True)
    except Exception as exc:
        log.exception("Transfer failed user=%s task=%s", job.user_id, job.task_id)
        if job.cancel_requested:
            job.set_state("paused")
        else:
            error_text = str(exc)
            if "HTTP 502" in error_text and "Sandbox does not exist" in error_text:
                error_text = (
                    "HTTP 502: el enlace apunta a un sandbox de Manus terminado. "
                    "Genera un enlace nuevo o envía el archivo directamente al bot."
                )
            elif "HTTP 502" in error_text:
                error_text = "HTTP 502: el servidor del enlace no está disponible ahora; prueba con otro enlace directo."
            job.error = error_text[:200]
            job.set_state("error")
        await _edit_task_status(job, task_detail_text(job))
    finally:
        if quota_reserved:
            release_data_quota_reservation(application, job.user_id, job.task_id)


async def task_worker(application: Application) -> None:
    manager: FairTaskQueue = application.bot_data["task_queue"]
    while True:
        job = await manager.take()
        await process_task(job, application)


# ═══════════════════════════════════════════════════════════════
#  COMANDOS PRINCIPALES
# ═══════════════════════════════════════════════════════════════
async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_update(update):
        return
    message, user = update.effective_message, update.effective_user
    if not message or not user:
        return
    if not await ensure_membership(update, context):
        return
    manager: FairTaskQueue = context.application.bot_data["task_queue"]
    states = {"queued", "downloading", "uploading", "cancelling", "paused"}
    jobs = manager.for_user(user.id, states)[:5]
    if not jobs:
        await message.reply_text(
            card("📋 ᴄᴏʟᴀ", ["No tienes tareas activas.", "", "Envía un archivo", "o enlace directo."]),
            reply_markup=home_keyboard(),
        )
        return
    keyboard = [[btn(task_state_label(j.state) + " - " + (j.filename or "Tarea")[:18],
                      callback_data=f"j:{j.task_id}:open")] for j in jobs]
    keyboard.append([btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")])
    await message.reply_text(
        card("📋 ᴄᴏʟᴀ", [task_summary(j) for j in jobs]),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_update(update):
        return
    message, user = update.effective_message, update.effective_user
    if not message or not user:
        return
    if not await ensure_membership(update, context):
        return
    # Combinar historial persistente + tareas en memoria
    persistent = get_task_history(user.id, 15)
    manager: FairTaskQueue = context.application.bot_data["task_queue"]
    memory_jobs = [j for j in manager.for_user(user.id) if j.state in {"done", "error", "cancelled"}]
    # Mostrar historial persistente
    if not persistent and not memory_jobs:
        await message.reply_text(
            card("📜 ʜɪꜱᴛᴏʀɪᴀʟ", ["No tienes historial", "todavía."]),
            reply_markup=home_keyboard(),
        )
        return
    lines = []
    for number, entry in enumerate(persistent[:10], 1):
        try:
            dt = datetime.fromisoformat(entry["completed_at"]).strftime("%d/%m %H:%M")
        except Exception:
            dt = "—"
        name = (entry.get("filename") or "Archivo")[:20]
        lines.append(f"{number}. ✅ {name}")
        lines.append(f"   {dt} · {_size_label(entry.get('size', 0), 1)}")
    box = card("📜 ʜɪꜱᴛᴏʀɪᴀʟ", lines or ["Sin registros."])
    rows = []
    # Botones para ver los últimos 5 enlaces
    for number, entry in enumerate(persistent[:5], 1):
        name = (entry.get("filename") or "Archivo")[:18]
        urls = entry.get("urls", [])
        if urls:
            rows.append([btn(f"📎 {number}. {name}", callback_data=f"history:view:{number - 1}")])
    rows.append([btn("🗑️ ʙᴏʀʀᴀʀ ʜɪꜱᴛᴏʀɪᴀʟ", callback_data="history:delete_menu")])
    rows.append([btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")])
    await message.reply_text(
        f"{box}\n\n{BOT_FOOTER}",
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )


async def downloads_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_update(update):
        return
    if update.effective_message and await ensure_membership(update, context):
        await update.effective_message.reply_text(
            download_menu_text(), reply_markup=download_menu_keyboard(),
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_update(update):
        return
    if update.effective_message and await ensure_membership(update, context):
        await update.effective_message.reply_text(help_page_text(0), reply_markup=help_keyboard(0))


async def my_quota_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_update(update):
        return
    user = update.effective_user
    if not user or not update.effective_message:
        return
    if not await ensure_membership(update, context):
        return
    box = card("📊 ᴍɪ ᴄᴜᴏᴛᴀ", quota_lines(user.id))
    await update.effective_message.reply_text(
        f"{box}\n\n{BOT_FOOTER}",
        reply_markup=home_keyboard(),
    )


def welcome_text(user_id: int, user_name: str, username: str | None = None) -> str:
    short_name = (user_name or "Usuario").split()[0][:14]
    username_label = f"@{username}" if username else "sin @usuario"
    identity_lines = [
        f"👤 Nombre: {user_name or 'Usuario'}",
        f"🔗 Usuario: {username_label}",
        f"🆔 ID: {{mono:{user_id}}}",
        "",
    ]
    if is_owner(user_id):
        lines = [
            f"👋 Hola, {short_name}",
            "",
            *identity_lines,
            f"🏷 Plan: 🜲 Plan Creador",
            f"📦 Disponible: ∞ GB",
            "",
            "📥 Envía archivo o enlace público.",
            "👑 Creador · ∞ GB",
        ]
        return card_with_credit(BOT_NAME, lines, preserve_title=True)
    plan = get_plan_info(get_user_plan(user_id))
    remaining = get_remaining_quota(user_id)
    plan_label = "Gratis" if plan["rank"] == 0 else f"{plan['badge']} {plan['display']}"
    lines = [
        f"👋 Hola, {short_name}",
        "",
        *identity_lines,
        f"🏷 Plan: {plan_label}",
        f"💰 Saldo: {get_user_balance(user_id)}$",
        f"📦 Disponible: {data_quota_label(remaining)}",
        "♾️ Sin vencimiento",
    ]
    if plan["rank"] > 0:
        _, rem_days = get_plan_expiry_info(user_id)
        if rem_days is not None:
            lines.append(f"⏳ Días restantes: {rem_days}")
    lines += [
        "",
        "📥 Envía un archivo o enlace",
        "   público para subirlo.",
    ]
    return card_with_credit(BOT_NAME, lines, preserve_title=True)


async def send_welcome_message(message, user_id: int, user_name: str, username: str | None,
                               reply_markup: InlineKeyboardMarkup, extra_text: str = "") -> None:
    """Muestra la imagen de marca y usa la ficha textual como fallback."""
    if WELCOME_IMAGE_PATH.is_file() and hasattr(message, "reply_photo"):
        with WELCOME_IMAGE_PATH.open("rb") as photo:
            await message.reply_photo(photo=photo, caption=extra_text or None, reply_markup=reply_markup)
        return
    await message.reply_text(
        welcome_text(user_id, user_name, username) + extra_text,
        reply_markup=reply_markup,
    )


async def edit_welcome_message(message, user_id: int, user_name: str, username: str | None,
                               reply_markup: InlineKeyboardMarkup, extra_text: str = "") -> None:
    """Cambia la pantalla de verificación por la imagen de marca."""
    if WELCOME_IMAGE_PATH.is_file() and hasattr(message, "edit_media"):
        with WELCOME_IMAGE_PATH.open("rb") as photo:
            await message.edit_media(
                media=InputMediaPhoto(media=photo, caption=extra_text or None),
                reply_markup=reply_markup,
            )
        return
    await message.edit_text(
        welcome_text(user_id, user_name, username) + extra_text,
        reply_markup=reply_markup,
    )


async def send_service_info_message(message, user_id: int, user_name: str,
                                    username: str | None) -> None:
    """Muestra la ficha sobre la imagen, sin volver a una pantalla textual aislada."""
    caption = welcome_text(user_id, user_name, username)
    markup = main_keyboard(user_id)
    if WELCOME_IMAGE_PATH.is_file() and hasattr(message, "reply_photo"):
        with WELCOME_IMAGE_PATH.open("rb") as photo:
            await message.reply_photo(photo=photo, caption=caption, reply_markup=markup)
        if hasattr(message, "delete"):
            try:
                await message.delete()
            except Exception:
                pass
        return
    await message.edit_text(caption, reply_markup=markup)


async def edit_query_message_text(message, text: str, **kwargs):
    """Edita caption si el menú actual es una foto; texto en cualquier otro caso."""
    if getattr(message, "photo", None) and hasattr(message, "edit_caption"):
        return await message.edit_caption(caption=text, **kwargs)
    return await message.edit_text(text, **kwargs)


def membership_keyboard(
    channel_label: str = "Canal oficial",
    group_label: str = "Soporte",
    todus_label: str = "Canal ToDusS3",
) -> InlineKeyboardMarkup:
    join_buttons = []
    if CHANNEL_JOIN_URL:
        join_buttons.append(btn(f"📢 {channel_label}", url=CHANNEL_JOIN_URL, style="success"))
    if GROUP_JOIN_URL:
        join_buttons.append(btn(f"👥 {group_label}", url=GROUP_JOIN_URL, style="success"))
    if TODUS_CHANNEL_JOIN_URL:
        join_buttons.append(btn(f"☁️ {todus_label}", url=TODUS_CHANNEL_JOIN_URL, style="success"))
    rows = [join_buttons[index:index + 2] for index in range(0, len(join_buttons), 2)]
    rows.append([btn("✅ Continuar al bot", callback_data="membership:verify", style="primary")])
    return InlineKeyboardMarkup(rows)


def membership_prompt_text(
    channel_label: str = "canal oficial",
    group_label: str = "grupo de soporte",
    todus_label: str = "canal ToDusS3",
) -> str:
    return card("🌐 ᴄᴀɴᴀʟᴇꜱ ᴏꜰɪᴄɪᴀʟᴇꜱ", [
        "Únete a nuestros canales oficiales",
        "para utilizar el servicio de",
        "descargas ToDusS3.",
        "",
        f"1️⃣ {channel_label}",
        f"2️⃣ {group_label}",
        f"3️⃣ {todus_label}",
        "",
        "Después de unirte a todos,",
        "Pulsa Continuar al bot ✅.",
    ])


async def required_chat_labels(bot_obj) -> tuple[str, str, str]:
    """Obtiene títulos reales de los chats obligatorios, con nombres legibles de respaldo."""
    defaults = ["Canal oficial", "Grupo soporte", "Canal ToDusS3"]
    configured = [chat for chat in (REQUIRED_CHANNEL, REQUIRED_GROUP, REQUIRED_TODUS_CHANNEL) if chat]
    labels = []
    for index, chat_id in enumerate(configured[:3]):
        label = None
        try:
            chat = await asyncio.wait_for(bot_obj.get_chat(chat_id), timeout=8)
            label = getattr(chat, "title", None) or getattr(chat, "username", None)
            if label and getattr(chat, "username", None) and not str(label).startswith("@"):
                label = f"@{label}"
        except Exception as exc:
            log.debug("No se pudo obtener el nombre del chat (%s)", type(exc).__name__)
        labels.append(str(label or defaults[index]))
    while len(labels) < 3:
        labels.append(defaults[len(labels)])
    return labels[0], labels[1], labels[2]


def membership_error_text() -> str:
    return card("⚠️ ᴠᴇʀɪꜰɪᴄᴀᴄɪóɴ", [
        "Telegram no permitió consultar",
        "tu membresía en este momento.",
        "El bot necesita acceso suficiente",
        "en los chats requeridos.",
    ])


def membership_missing_text(labels: list[str]) -> str:
    missing = labels or ["canal obligatorio"]
    lines = [f"No estás dentro de {label}." for label in missing]
    lines += ["", "Únete y pulsa «Ya me uní». "]
    return card("🚫 ᴀᴄᴄᴇꜱᴏ ʀᴇꜱᴛʀɪɴɢɪᴅᴏ", lines)


async def check_required_memberships_detailed(bot_obj, user_id: int) -> tuple[bool, bool, list[str]]:
    """Comprueba cada chat y devuelve también los chats donde falta el usuario.

    La Bot API no garantiza consultar miembros de un canal si el bot no tiene
    permisos suficientes. En ese caso se deniega el acceso y se muestra un
    error de verificación, nunca se concede acceso silenciosamente.
    """
    required = [chat for chat in (REQUIRED_CHANNEL, REQUIRED_GROUP, REQUIRED_TODUS_CHANNEL) if chat]
    labels = [str(chat) if str(chat).startswith("@") else "el canal configurado" for chat in required]
    missing: list[str] = []
    failed = False

    async def check_one(index: int, chat_id: str):
        nonlocal failed
        try:
            member = await asyncio.wait_for(
                bot_obj.get_chat_member(chat_id=chat_id, user_id=user_id),
                timeout=8,
            )
            status = getattr(member, "status", "")
            is_member = status in {"creator", "administrator", "member"}
            if status == "restricted":
                is_member = bool(getattr(member, "is_member", False))
            if not is_member:
                missing.append(labels[index] if index < len(labels) else str(chat_id))
        except Exception as exc:
            failed = True
            log.warning("No se pudo verificar una membresía (%s)", type(exc).__name__)

    await asyncio.gather(*(check_one(index, chat_id) for index, chat_id in enumerate(required)))
    return not missing and not failed, failed, missing


async def check_required_memberships(bot_obj, user_id: int) -> tuple[bool, bool]:
    """Devuelve (pertenece_a_todos, fallo_de_consulta)."""
    is_member, check_failed, _missing = await check_required_memberships_detailed(bot_obj, user_id)
    return is_member, check_failed


async def user_has_required_memberships(bot_obj, user_id: int) -> bool:
    """Los canales son informativos: la membresía no bloquea el uso del bot."""
    return True


async def ensure_membership(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    allow_owner: bool = True,
) -> bool:
    """Conserva compatibilidad con handlers antiguos; la membresía es opcional."""
    if not is_private_update(update):
        return False
    user = update.effective_user
    message = getattr(update, "effective_message", None)
    if user and is_user_blocked(user.id):
        if message:
            await message.reply_text(blocked_user_text())
        return False
    if user:
        register_user(user)
    return True


async def complete_pending_referral(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Acredita el referido pendiente y avisa al invitador al continuar al bot."""
    referrer_id = apply_pending_referral(user_id)
    if referrer_id is None:
        return False
    schedule_data_backup(context.application, "recompensa de referido")
    await publish_channel_notice(
        context.bot,
        "🎉 ɴᴜᴇᴠᴏ ʀᴇꜰᴇʀɪᴅᴏ",
        f"Invitador: {public_user_label(referrer_id)}\n"
        f"Referido: {public_user_label(user_id)}\n"
        "Se confirmó el referido y se acreditó saldo.",
    )
    try:
        await context.bot.send_message(
            chat_id=referrer_id,
            text=card("🎉 ʀᴇꜰᴇʀɪᴅᴏ ᴄᴏɴꜰɪʀᴍᴀᴅᴏ", [
                "Alguien entró con tu enlace.",
                "🎊 ¡Felicidades! Recibiste 5$.",
                f"💳 Saldo actual: {get_user_balance(referrer_id)}$",
            ]),
        )
    except Exception:
        log.info("No se pudo avisar al invitador %s", referrer_id)
    return True


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_update(update):
        return
    user = update.effective_user
    if not user or not update.effective_message:
        return
    if is_user_blocked(user.id):
        await update.effective_message.reply_text(blocked_user_text())
        return
    log.info("Received /start from user=%s", user.id)
    args = getattr(context, "args", []) or []
    if args:
        match = re.fullmatch(r"ref_(\d{1,20})", str(args[0]).strip(), re.IGNORECASE)
        if match:
            remember_pending_referral(user.id, int(match.group(1)))
    is_new_user = register_user(user)
    if is_new_user:
        schedule_data_backup(context.application, "registro de usuario nuevo")
    if channel_prompt_completed(user.id):
        referral_notice = ""
        if await complete_pending_referral(user.id, context):
            referral_notice = "\n\n🎁 Recibiste 1 GB gratis y 5$ de bienvenida por entrar con un enlace de invitación."
        await send_welcome_message(
            update.effective_message,
            user.id,
            getattr(user, "full_name", "") or getattr(user, "first_name", ""),
            getattr(user, "username", None),
            main_keyboard(user.id),
            referral_notice,
        )
        return
    await update.effective_message.reply_text(
        membership_prompt_text(),
        reply_markup=membership_keyboard(),
    )


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_update(update):
        return
    user = update.effective_user
    if user and update.effective_message:
        if not await ensure_membership(update, context):
            return
        username = f"@{user.username}" if user.username else "sin @usuario"
        await update.effective_message.reply_text(
            card("👤 ᴛᴜ ᴜꜱᴜᴀʀɪᴏ ʏ ᴛᴜ ɪᴅ", [
                f"Usuario: {{mono:{username}}}",
                f"ID de Telegram: {{mono:{user.id}}}",
            ]),
        )


async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_update(update):
        return
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not await ensure_membership(update, context):
        return
    await message.reply_text(
        public_leaderboard_text(),
        reply_markup=InlineKeyboardMarkup([[btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")]]),
    )


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_update(update):
        return
    """Ejecuta una copia inmediata y devuelve un diagnóstico seguro al dueño."""
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not is_owner(user.id):
        return
    chat_id = configured_backup_chat_id()
    layout = context.application.bot_data.get("telegram_backup_layout")
    if not chat_id or layout is None:
        await message.reply_text(sc("Respaldo no configurado: falta TELEGRAM_BACKUP_CHAT_ID."))
        return
    lock = context.application.bot_data.get("telegram_backup_lock")
    if lock is None:
        lock = asyncio.Lock()
        context.application.bot_data["telegram_backup_lock"] = lock
    try:
        async with lock:
            await backup_to_telegram(
                context.bot,
                getattr(context.bot, "token", ""),
                chat_id,
                layout,
                logger=log,
            )
        await message.reply_text(sc("✅ Respaldo cifrado enviado y validado."))
    except Exception as exc:
        log.warning("Prueba manual de respaldo fallida (%s).", type(exc).__name__)
        await message.reply_text(sc("❌ No se pudo enviar el respaldo. Revisa permisos y TELEGRAM_BACKUP_CHAT_ID."))


async def jkadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_update(update):
        return
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not is_owner(user.id):
        return
    if not await ensure_membership(update, context):
        return
    if update.effective_chat.type != "private":
        return
    await message.reply_text(
        admin_panel_text(),
        reply_markup=admin_keyboard(),
    )


# ═══════════════════════════════════════════════════════════════
#  MANEJADOR DE CALLBACKS
# ═══════════════════════════════════════════════════════════════
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_update(update):
        return
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    if is_user_blocked(user.id):
        await query.answer(sc("Tu usuario está bloqueado"), show_alert=True)
        if hasattr(query.message, "reply_text"):
            await query.message.reply_text(blocked_user_text())
        return
    register_user(user)
    if query.data == "membership:verify":
        await query.answer()
        mark_channel_prompt_completed(user.id)
        referral_notice = ""
        if await complete_pending_referral(user.id, context):
            referral_notice = "\n\n🎁 Recibiste 1 GB gratis y 5$ de bienvenida por entrar con un enlace de invitación."
        await edit_welcome_message(
            query.message,
            user.id,
            getattr(user, "full_name", "") or getattr(user, "first_name", ""),
            getattr(user, "username", None),
            main_keyboard(user.id),
            referral_notice,
        )
        return
    # El saldo/planes debe abrirse directamente desde la botonera principal;
    # no debe quedar bloqueado por una comprobación de membresía que falle.
    if query.data in {"balance", "plans", "referral:show"} or query.data.startswith(("plans:buy", "plans:cash", "plans:confirm:", "plans:purchase:", "plans:unavailable:")):
        await query.answer()
        await edit_query_message_text(query.message,
            plans_text(user.id, context.application.bot_data.get("bot_username")),
            reply_markup=plans_keyboard(user.id, context.application.bot_data.get("bot_username")),
        )
        return
    if query.data.startswith("data_package:select:"):
        package_key = query.data.rsplit(":", 1)[-1]
        if package_key not in DATA_PACKAGES or is_owner(user.id):
            await query.answer(sc("Paquete no disponible"), show_alert=True)
            return
        await query.answer()
        await edit_query_message_text(query.message,
            data_package_confirmation_text(user.id, package_key),
            reply_markup=data_package_confirmation_keyboard(package_key),
        )
        return
    if query.data.startswith("data_package:confirm:"):
        package_key = query.data.rsplit(":", 1)[-1]
        if package_key not in DATA_PACKAGES or is_owner(user.id):
            await query.answer(sc("Paquete no disponible"), show_alert=True)
            return
        ok, reason, new_balance, new_quota = purchase_data_package(user.id, package_key)
        if not ok:
            if reason == "insufficient":
                await query.answer(sc("Saldo insuficiente"), show_alert=True)
            else:
                await query.answer(sc("No se pudo completar la compra"), show_alert=True)
            await edit_query_message_text(query.message,
                plans_text(user.id, context.application.bot_data.get("bot_username")),
                reply_markup=plans_keyboard(user.id, context.application.bot_data.get("bot_username")),
            )
            return
        await query.answer(sc("Paquete comprado"))
        schedule_data_backup(context.application, "paquete de datos comprado")
        package = DATA_PACKAGES[package_key]
        await edit_query_message_text(query.message,
            card("✅ ᴄᴏᴍᴘʀᴀ ᴄᴏᴍᴘʟᴇᴛᴀ", [
                f"Paquete: {package['name']}",
                f"Descontado: {package['price']}$",
                f"Saldo actual: {new_balance}$",
                f"Datos disponibles: {data_quota_label(new_quota)}",
                "No vence por tiempo; se gasta al transferir.",
            ]) + f"\n\n{BOT_FOOTER}",
            reply_markup=plans_keyboard(user.id, context.application.bot_data.get("bot_username")),
        )
        return
    if not is_owner(user.id) and not await user_has_required_memberships(context.bot, user.id):
        await query.answer()
        labels = await required_chat_labels(context.bot)
        await edit_query_message_text(query.message,
            membership_prompt_text(*labels),
            reply_markup=membership_keyboard(*labels),
        )
        return
    await query.answer()

    if query.data == "top:users":
        await edit_query_message_text(query.message,
            public_leaderboard_text(),
            reply_markup=InlineKeyboardMarkup([[btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")]]),
        )
        return

    if query.data == "all_menu":
        await edit_query_message_text(query.message,
            card("📄 ᴍᴇɴᴜ́ ᴄᴏᴍᴘʟᴇᴛᴏ", [
                "Elige una función del bot:",
                "subir archivos, revisar tu cuota,",
                "consultar referidos o ver ayuda.",
            ]),
            reply_markup=all_menu_keyboard(),
        )
        return

    if query.data == "action_menu":
        await edit_query_message_text(query.message,
            card("⚙️ ᴀᴄᴄɪᴏɴᴇꜱ", [
                "Gestiona tus transferencias,",
                "consulta tu saldo y revisa los datos.",
            ]),
            reply_markup=action_menu_keyboard(),
        )
        return

    if query.data == "my_info":
        await send_service_info_message(
            query.message,
            user.id,
            getattr(user, "full_name", "") or getattr(user, "first_name", ""),
            getattr(user, "username", None),
        )
        return

    if query.data == "service_info":
        await send_service_info_message(
            query.message,
            user.id,
            getattr(user, "full_name", "") or getattr(user, "first_name", ""),
            getattr(user, "username", None),
        )
        return

    if query.data in {"balance", "plans", "referral:show"}:
        await edit_query_message_text(query.message,
            plans_text(user.id, context.application.bot_data.get("bot_username")),
            reply_markup=plans_keyboard(user.id, context.application.bot_data.get("bot_username")),
        )
        return

    if query.data.startswith("admin:"):
        if not is_owner(user.id):
            await query.answer(sc("Sin acceso"), show_alert=True)
            return

        if query.data == "admin:open":
            await edit_query_message_text(query.message, admin_panel_text(), reply_markup=admin_keyboard())
            return

        if query.data == "admin:back":
            context.user_data.pop("admin_state", None)
            await edit_query_message_text(query.message, admin_panel_text(), reply_markup=admin_keyboard())
            return

        if query.data == "admin:balance":
            context.user_data["admin_state"] = {"step": "awaiting_balance_user_id"}
            await edit_query_message_text(query.message,
                card("💰 ᴅᴀʀ ꜱᴀʟᴅᴏ", [
                    "Envía el ID numérico",
                    "del usuario destinatario.",
                    "",
                    "Después indicarás el monto",
                    "y confirmarás la operación.",
                ]),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data.startswith("admin:confirm_balance:"):
            parts = query.data.split(":")
            if len(parts) != 4:
                await query.answer(sc("Operación inválida"), show_alert=True)
                return
            target_id, amount = int(parts[2]), int(parts[3])
            context.user_data.pop("admin_state", None)
            ok, new_balance = grant_user_balance(target_id, amount)
            if not ok:
                await query.answer(sc("No se pudo guardar el saldo"), show_alert=True)
                return
            schedule_data_backup(context.application, "saldo otorgado por administración")
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=card("🎁 ꜱᴀʟᴅᴏ ʀᴇᴄɪʙɪᴅᴏ", [
                        f"Has recibido {amount}$ de saldo.",
                        f"💰 Saldo actual: {new_balance}$",
                    ]),
                )
            except Exception:
                log.info("No se pudo avisar del saldo al usuario %s", target_id)
            await edit_query_message_text(query.message,
                card("✅ ꜱᴀʟᴅᴏ ᴏᴛᴏʀɢᴀᴅᴏ", [
                    f"ID: {{mono:{target_id}}}",
                    f"Acreditado: {amount}$",
                    f"Saldo actual: {new_balance}$",
                ]),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data == "admin:tutorial":
            await edit_query_message_text(query.message,
                admin_tutorial_text(),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data == "admin:stats":
            plan_data = _load_json(PLAN_DATA_FILE)
            users_data = _load_json(USERS_DATA_FILE).get("users", {})
            known_ids = registered_user_ids()
            total_users = len(known_ids)
            today = _today_key()
            active_today = sum(
                1 for uid in known_ids
                if isinstance(users_data, dict)
                and str(uid) in users_data
                and users_data[str(uid)].get("last_seen_date") == today
            )
            by_plan = {}
            for uid in known_ids:
                info = plan_data.get(str(uid), {"plan": "free"})
                p = info.get("plan", "free")
                by_plan[p] = by_plan.get(p, 0) + 1
            lines = [
                f"👥 Usuarios: {total_users}",
                f"🟢 Activos hoy: {active_today}",
                "",
                "Por plan:",
            ]
            for pk, count in sorted(by_plan.items()):
                pi = get_plan_info(pk)
                lines.append(f"{pi['badge']} {pi['display']}: {count}")
            box = card("📊 ᴇꜱᴛᴀᴅíꜱᴛɪᴄᴀꜱ", lines)
            await edit_query_message_text(query.message,
                f"{box}\n\n{BOT_FOOTER}",
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data == "admin:top":
            await edit_query_message_text(query.message,
                admin_leaderboard_text(),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data.startswith("admin:users:"):
            page = max(0, int(query.data.split(":")[-1]))
            await edit_query_message_text(query.message,
                admin_registered_users_text(page),
                reply_markup=admin_registered_users_keyboard(page),
            )
            return

        if query.data.startswith("admin:user:"):
            target_id = int(query.data.split(":")[-1])
            await edit_query_message_text(query.message,
                admin_user_history_text(target_id),
                reply_markup=admin_user_history_keyboard(target_id),
            )
            return

        if query.data == "admin:user_history":
            context.user_data["admin_state"] = {"step": "admin_history_user"}
            await edit_query_message_text(query.message,
                card("🗂️ ʜɪꜱᴛᴏʀɪᴀʟ ᴅᴇ ᴜꜱᴜᴀʀɪᴏ", ["Envía el ID numérico", "del usuario que quieres gestionar."]),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data == "admin:cloud_user":
            context.user_data["admin_state"] = {"step": "admin_cloud_user"}
            await edit_query_message_text(query.message,
                card("🗑️ ʙᴏʀʀᴀʀ ᴀʀᴄʜɪᴠᴏ", [
                    "Envía el ID numérico del usuario.",
                    "Después podrás elegir el archivo",
                    "y borrarlo de ToDusS3.",
                ]),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data == "admin:cloud_all_confirm":
            total = sum(
                len(entry.get("urls", []))
                for entries in all_task_history().values()
                if isinstance(entries, list)
                for entry in entries
                if isinstance(entry, dict) and isinstance(entry.get("urls"), list)
            )
            await edit_query_message_text(query.message,
                card("⚠️ ʙᴏʀʀᴀʀ ᴛᴏᴅᴏ ᴇʟ ᴀʀᴄʜɪᴠᴏ", [
                    f"Se eliminarán aproximadamente {total} archivos",
                    "subidos a la nube por los usuarios.",
                    "",
                    "Esta acción no se puede deshacer.",
                ]),
                reply_markup=InlineKeyboardMarkup([
                    [btn("✅ ʙᴏʀʀᴀʀ ᴛᴏᴅᴏ", callback_data="admin:cloud_all_yes")],
                    [btn("❌ ᴄᴀɴᴄᴇʟᴀʀ", callback_data="admin:back")],
                ]),
            )
            return

        if query.data == "admin:cloud_all_yes":
            total, deleted, failed = await delete_all_cloud_files(context.application)
            affected = clear_all_task_history() if failed == 0 else 0
            if failed == 0:
                schedule_data_backup(context.application, "borrado global de archivos de nube")
            await edit_query_message_text(query.message,
                card("✅ ʙᴏʀʀᴀᴅᴏ ᴅᴇ ɴᴜʙᴇ ᴄᴏᴍᴘʟᴇᴛᴀᴅᴏ", [
                    f"Archivos encontrados: {total}",
                    f"Eliminados: {deleted}",
                    f"Fallos: {failed}",
                    f"Usuarios afectados: {affected}" if failed == 0 else "El historial se conservó para reintentar.",
                ]),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data in {"admin:block", "admin:unblock"}:
            context.user_data["admin_state"] = {
                "step": "admin_block_user" if query.data == "admin:block" else "admin_unblock_user"
            }
            action = "bloquear" if query.data == "admin:block" else "desbloquear"
            await edit_query_message_text(query.message,
                card("🛡️ ɢᴇꜱᴛɪᴏɴᴀʀ ᴀᴄᴄᴇꜱᴏ", [
                    f"Envía el ID del usuario que deseas {action}.",
                    "Solo se aceptan IDs numéricos positivos.",
                ]),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data == "admin:all_history_confirm":
            total_entries = sum(len(entries) for entries in all_task_history().values() if isinstance(entries, list))
            await edit_query_message_text(query.message,
                card("⚠️ ʙᴏʀʀᴀᴅᴏ ɢʟᴏʙᴀʟ", [
                    f"Se eliminarán {total_entries} registros",
                    "y sus archivos de ToDus S3.",
                    "",
                    "Esta acción no se puede deshacer.",
                ]),
                reply_markup=InlineKeyboardMarkup([
                    [btn("✅ ʙᴏʀʀᴀʀ ᴛᴏᴅᴏ", callback_data="admin:all_history_yes")],
                    [btn("❌ ᴄᴀɴᴄᴇʟᴀʀ", callback_data="admin:back")],
                ]),
            )
            return

        if query.data == "admin:all_history_yes":
            snapshot = all_task_history()
            deleted_cloud = 0
            for entries in snapshot.values():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    urls = entry.get("urls", []) if isinstance(entry, dict) else []
                    for url in urls if isinstance(urls, list) else []:
                        try:
                            await asyncio.to_thread(delete_from_todus_s3, url)
                            deleted_cloud += 1
                        except Exception:
                            log.warning("No se pudo borrar objeto global %s", url)
            affected = clear_all_task_history()
            schedule_data_backup(context.application, "borrado global de historial")
            await edit_query_message_text(query.message,
                card("✅ ʜɪꜱᴛᴏʀɪᴀʟ ʙᴏʀʀᴀᴅᴏ", [
                    f"Usuarios afectados: {affected}",
                    f"Enlaces eliminados de ToDus: {deleted_cloud}",
                    "",
                    "El historial local quedó vacío.",
                ]),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data.startswith("admin:histdel:"):
            _, _, raw_user, raw_index = query.data.split(":")
            target_id, index = int(raw_user), int(raw_index)
            entries = get_task_history(target_id, 50)
            if 0 <= index < len(entries):
                entry = entries[index]
                for url in entry.get("urls", []) if isinstance(entry.get("urls"), list) else []:
                    try:
                        await asyncio.to_thread(delete_from_todus_s3, url)
                    except Exception:
                        log.warning("No se pudo borrar objeto admin %s", url)
                delete_task_history_entry(target_id, index)
            await edit_query_message_text(query.message, admin_user_history_text(target_id), reply_markup=admin_user_history_keyboard(target_id))
            return

        if query.data.startswith("admin:histclear:"):
            target_id = int(query.data.split(":")[-1])
            entries = get_task_history(target_id, 50)
            for entry in entries:
                for url in entry.get("urls", []) if isinstance(entry.get("urls"), list) else []:
                    try:
                        await asyncio.to_thread(delete_from_todus_s3, url)
                    except Exception:
                        log.warning("No se pudo borrar objeto admin %s", url)
            clear_task_history(target_id)
            await edit_query_message_text(query.message, admin_user_history_text(target_id), reply_markup=admin_user_history_keyboard(target_id))
            return

        if query.data.startswith("admin:quotareset:"):
            target_id = int(query.data.split(":")[-1])
            reset_user_daily_usage(target_id)
            await edit_query_message_text(query.message, admin_user_history_text(target_id), reply_markup=admin_user_history_keyboard(target_id))
            return

        if query.data == "admin:broadcast":
            context.user_data["admin_state"] = {"step": "broadcast_message"}
            await edit_query_message_text(query.message,
                card("📢 ᴅɪꜰᴜꜱɪóɴ", [
                    "Escribe el mensaje que",
                    "recibirán los usuarios",
                    "registrados en el bot.",
                    "",
                    "Se enviará como:",
                    "Mensaje del administrador",
                ]),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data == "admin:report":
            await edit_query_message_text(query.message,
                admin_report_text(),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data == "admin:check":
            context.user_data["admin_state"] = {"step": "check_user"}
            lines = [
                "Envía el ID numérico",
                "de Telegram del",
                "usuario a consultar.",
                "",
                "Ejemplo: 123456789",
            ]
            await edit_query_message_text(query.message,
                card("🔍 ᴄᴏɴꜱᴜʟᴛᴀʀ ᴜꜱᴜᴀʀɪᴏ", lines),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data.startswith("admin:plan:"):
            plan_key = query.data.split(":", 2)[2]
            if plan_key not in PLANS:
                return
            if plan_key == "free":
                context.user_data["admin_state"] = {"step": "awaiting_user_id", "plan": "free", "days": 0}
                lines = [
                    "Plan: 🆓 Plan Gratis",
                    "Duración: Permanente",
                    "",
                    "Envía el ID de",
                    "Telegram del usuario.",
                ]
                await edit_query_message_text(query.message,
                    card("🆓 ᴀꜱɪɢɴᴀʀ ɢʀᴀᴛɪꜱ", lines),
                    reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
                )
            else:
                plan = get_plan_info(plan_key)
                lines = [
                    f"Plan: {plan['badge']} {plan['display']}",
                    f"Cuota: {plan['daily_label']}/día",
                    "",
                    "Selecciona el tiempo",
                    "de duración:",
                ]
                await edit_query_message_text(query.message,
                    card(f"⏳ ᴛɪᴇᴍᴘᴏ · {plan['display']}", lines),
                    reply_markup=admin_duration_keyboard(plan_key),
                )
            return

        if query.data.startswith("admin:dur:"):
            parts = query.data.split(":")
            plan_key = parts[2]
            days = int(parts[3])
            context.user_data["admin_state"] = {"step": "awaiting_user_id", "plan": plan_key, "days": days}
            plan = get_plan_info(plan_key)
            lines = [
                f"Plan: {plan['badge']} {plan['display']}",
                f"Duración: {days} días",
                f"Cuota: {plan['daily_label']}/día",
                "",
                "Envía ahora el ID",
                "de Telegram del",
                "usuario.",
            ]
            await edit_query_message_text(query.message,
                card("🆔 ɪɴɢʀᴇꜱᴀʀ ᴜꜱᴜᴀʀɪᴏ", lines),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data.startswith("admin:dur_custom:"):
            plan_key = query.data.split(":", 2)[2]
            context.user_data["admin_state"] = {"step": "awaiting_custom_days", "plan": plan_key}
            lines = [
                f"Plan: {PLANS[plan_key]['display']}",
                "",
                "Escribe el número de",
                "días que deseas otorgar.",
                "",
                "Ejemplo: 45, 60, 365...",
            ]
            await edit_query_message_text(query.message,
                card("✏️ ᴅíᴀꜱ ᴘᴇʀꜱᴏɴᴀʟɪᴢᴀᴅᴏꜱ", lines),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if query.data.startswith("admin:confirm_plan:"):
            parts = query.data.split(":")
            plan_key = parts[2]
            days = int(parts[3])
            target_id = int(parts[4])
            context.user_data.pop("admin_state", None)

            entry = set_user_plan(target_id, plan_key, days if days > 0 else None)
            record_admin_audit(user.id, target_id, plan_key, days if days > 0 else None)
            schedule_data_backup(context.application, "cambio de plan")
            plan = get_plan_info(plan_key)
            exp_date, _ = get_plan_expiry_info(target_id)

            lines_admin = [
                f"Usuario: {{mono:{target_id}}}",
                f"Plan: {plan['badge']} {plan['display']}",
                f"Cuota: {plan['daily_label']}/día",
                f"Duración: {f'{days} días' if days > 0 else 'Permanente'}",
            ]
            if exp_date:
                lines_admin.append(f"Vence: {exp_date}")
            lines_admin.append("")
            lines_admin.append("💾 Guardado correctamente")

            await edit_query_message_text(query.message,
                card("✅ ᴘʟᴀɴ ᴀꜱɪɢɴᴀᴅᴏ", lines_admin),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )

            try:
                user_msg_lines = [
                    "¡Felicidades!",
                    "",
                    f"Usted adquirió el plan",
                    f"{plan['badge']} {plan['display']}",
                    f"por {days} días." if days > 0 else "de forma permanente.",
                    "",
                    f"📦 Cuota: {plan['daily_label']}/día",
                ]
                if exp_date:
                    user_msg_lines.append(f"📅 Vence el: {exp_date}")
                user_msg_lines += [
                    "",
                    "⚡ ¡Disfruta al máximo",
                    "tus descargas!",
                ]
                box_user = card("👑 ᴘʟᴀɴ ᴀᴄᴛɪᴠᴀᴅᴏ", user_msg_lines)
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"{box_user}\n\n{BOT_FOOTER}",
                    reply_markup=home_keyboard(),
                )
            except Exception:
                log.exception("No se pudo notificar al usuario %s", target_id)
            return
        return

    if query.data.startswith("help:"):
        try:
            page = int(query.data.split(":", 1)[1])
            if page < 0 or page >= len(HELP_PAGES):
                raise ValueError
        except ValueError:
            return
        await edit_query_message_text(query.message, help_page_text(page), reply_markup=help_keyboard(page))
        return

    if query.data == "my_quota":
        box = card("📊 ᴍɪ ᴄᴜᴏᴛᴀ", quota_lines(user.id))
        await edit_query_message_text(query.message,
            f"{box}\n\n{BOT_FOOTER}",
            reply_markup=home_keyboard(),
        )
        return

    if query.data == "downloads":
        await edit_query_message_text(query.message, download_menu_text(), reply_markup=download_menu_keyboard())
        return

    if query.data in {"download:telegram", "download:url"}:
        source = query.data.split(":", 1)[1]
        await edit_query_message_text(query.message, download_prompt_text(source), reply_markup=download_prompt_keyboard())
        return

    if query.data == "open_queue":
        manager: FairTaskQueue = context.application.bot_data["task_queue"]
        jobs = manager.for_user(user.id, {"queued", "downloading", "uploading", "cancelling", "paused"})[:5]
        rows = [[btn(task_state_label(j.state) + " - " + (j.filename or "Tarea")[:18],
                      callback_data=f"j:{j.task_id}:open")] for j in jobs]
        rows.append([btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")])
        lines = [task_summary(j) for j in jobs] if jobs else ["No hay tareas activas."]
        await edit_query_message_text(query.message,
            card("📋 ᴄᴏʟᴀ", lines), reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if query.data == "open_history":
        persistent = get_task_history(user.id, 10)
        if not persistent:
            await edit_query_message_text(query.message,
                card("📜 ʜɪꜱᴛᴏʀɪᴀʟ", ["No tienes historial", "todavía."]),
                reply_markup=home_keyboard(),
            )
            return
        lines = []
        for number, entry in enumerate(persistent, 1):
            try:
                dt = datetime.fromisoformat(entry["completed_at"]).strftime("%d/%m %H:%M")
            except Exception:
                dt = "—"
            name = (entry.get("filename") or "Archivo")[:20]
            lines.append(f"{number}. ✅ {name}")
            lines.append(f"   {dt} · {_size_label(entry.get('size', 0), 1)}")
        rows = []
        for number, entry in enumerate(persistent[:5], 1):
            name = (entry.get("filename") or "Archivo")[:18]
            urls = entry.get("urls", [])
            if urls:
                rows.append([btn(f"📎 {number}. {name}", callback_data=f"history:view:{number - 1}")])
        rows.append([btn("🗑️ ʙᴏʀʀᴀʀ ʜɪꜱᴛᴏʀɪᴀʟ", callback_data="history:delete_menu")])
        rows.append([btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")])
        box = card("📜 ʜɪꜱᴛᴏʀɪᴀʟ", lines)
        await edit_query_message_text(query.message,
            f"{box}\n\n{BOT_FOOTER}",
            reply_markup=InlineKeyboardMarkup(rows),
            disable_web_page_preview=True,
        )
        return

    if query.data.startswith("history:view:"):
        try:
            index = int(query.data.rsplit(":", 1)[1])
            entry = get_task_history(user.id, 50)[index]
        except (ValueError, IndexError, TypeError):
            await query.answer(sc("Ese historial ya no existe"), show_alert=True)
            return
        await edit_query_message_text(query.message,
            transfer_complete_text(
                filename=entry.get("filename", "Archivo"),
                size=entry.get("size", 0),
                parts=entry.get("parts", 1),
                duration=entry.get("duration"),
                expires_at=entry.get("expires_at"),
            ),
            reply_markup=history_detail_keyboard(entry, index),
            disable_web_page_preview=True,
        )
        return

    if query.data.startswith("history:cloud_delete:"):
        try:
            index = int(query.data.rsplit(":", 1)[1])
            entry = get_task_history(user.id, 50)[index]
        except (ValueError, IndexError, TypeError):
            await query.answer(sc("Ese historial ya no existe"), show_alert=True)
            return
        urls = entry.get("urls", []) if isinstance(entry.get("urls", []), list) else []
        if not urls:
            delete_task_history_entry(user.id, index)
            schedule_data_backup(context.application, "borrado de historial")
            await edit_query_message_text(query.message, card("✅ ʜɪꜱᴛᴏʀɪᴀʟ ʙᴏʀʀᴀᴅᴏ", ["No había archivos para borrar."]), reply_markup=home_keyboard())
            return
        results = await asyncio.gather(*(
            asyncio.to_thread(delete_from_todus_s3, url) for url in urls
        ), return_exceptions=True)
        failed = [result for result in results if isinstance(result, Exception)]
        if failed:
            await query.answer(sc("No se pudo borrar todo de ToDus"), show_alert=True)
            return
        delete_task_history_entry(user.id, index)
        schedule_data_backup(context.application, "borrado de archivo")
        await edit_query_message_text(query.message,
            card("✅ ᴀʀᴄʜɪᴠᴏ ʙᴏʀʀᴀᴅᴏ", [
                "El archivo y su registro",
                "fueron eliminados.",
            ]),
            reply_markup=home_keyboard(),
        )
        return

    if query.data == "history:delete_menu":
        entries = get_task_history(user.id, 10)
        if not entries:
            await edit_query_message_text(query.message,
                card("🗑️ ʙᴏʀʀᴀʀ ʜɪꜱᴛᴏʀɪᴀʟ", ["No hay registros", "para eliminar."]),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ʜɪꜱᴛᴏʀɪᴀʟ", callback_data="open_history")]]),
            )
            return
        lines = ["Selecciona qué registro", "quieres eliminar:", ""]
        rows = []
        for number, entry in enumerate(entries, 1):
            name = (entry.get("filename") or "Archivo")[:24]
            lines.append(f"{number}. {name}")
            rows.append([btn(f"🗑️ ᴇʟɪᴍɪɴᴀʀ {number}", callback_data=f"history:delete:{number - 1}")])
        rows.append([btn("🧹 ʙᴏʀʀᴀʀ ᴛᴏᴅᴏ ᴇʟ ʜɪꜱᴛᴏʀɪᴀʟ", callback_data="history:clear:confirm")])
        rows.append([btn("⬅️ ʜɪꜱᴛᴏʀɪᴀʟ", callback_data="open_history")])
        await edit_query_message_text(query.message,
            card("🗑️ ʙᴏʀʀᴀʀ ʜɪꜱᴛᴏʀɪᴀʟ", lines),
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if query.data.startswith("history:delete:"):
        try:
            index = int(query.data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer(sc("Registro inválido"), show_alert=True)
            return
        entries = get_task_history(user.id, 50)
        if index < 0 or index >= len(entries):
            await query.answer(sc("Ese registro ya no existe"), show_alert=True)
            return
        if not await delete_history_entry_cloud(entries[index]):
            await query.answer(
                sc("No se pudo borrar el archivo de la nube; el historial sigue intacto."),
                show_alert=True,
            )
            return
        if not delete_task_history_entry(user.id, index):
            await query.answer(sc("El historial cambió; inténtalo otra vez."), show_alert=True)
            return
        schedule_data_backup(context.application, "borrado de historial")
        await query.answer(sc("Historial eliminado"))
        entries = get_task_history(user.id, 10)
        if not entries:
            await edit_query_message_text(query.message,
                card("📜 ʜɪꜱᴛᴏʀɪᴀʟ", ["El registro fue eliminado.", "No quedan descargas guardadas."]),
                reply_markup=home_keyboard(),
            )
            return
        rows = []
        lines = ["Selecciona qué registro", "quieres eliminar:", ""]
        for number, entry in enumerate(entries, 1):
            name = (entry.get("filename") or "Archivo")[:24]
            lines.append(f"{number}. {name}")
            rows.append([btn(f"🗑️ ᴇʟɪᴍɪɴᴀʀ {number}", callback_data=f"history:delete:{number - 1}")])
        rows.append([btn("🧹 ʙᴏʀʀᴀʀ ᴛᴏᴅᴏ ᴇʟ ʜɪꜱᴛᴏʀɪᴀʟ", callback_data="history:clear:confirm")])
        rows.append([btn("⬅️ ʜɪꜱᴛᴏʀɪᴀʟ", callback_data="open_history")])
        await edit_query_message_text(query.message, card("🗑️ ʙᴏʀʀᴀʀ ʜɪꜱᴛᴏʀɪᴀʟ", lines), reply_markup=InlineKeyboardMarkup(rows))
        return

    if query.data == "history:clear:confirm":
        await edit_query_message_text(query.message,
            card("⚠️ ᴄᴏɴꜰɪʀᴍᴀʀ", [
                "¿Borrar todo el historial?",
                "",
                "Se borrarán los registros",
                "y también sus archivos",
                "guardados en ToDus.",
            ]),
            reply_markup=InlineKeyboardMarkup([
                [btn("✅ ʙᴏʀʀᴀʀ ᴛᴏᴅᴏ", callback_data="history:clear:yes")],
                [btn("❌ ᴄᴀɴᴄᴇʟᴀʀ", callback_data="history:delete_menu")],
            ]),
        )
        return

    if query.data == "history:clear:yes":
        entries = get_task_history(user.id, 50)
        results = await asyncio.gather(*(
            delete_history_entry_cloud(entry) for entry in entries
        ))
        if not all(results):
            await edit_query_message_text(query.message,
                card("⚠️ ɴᴏ ꜱᴇ ᴘᴜᴅᴏ ʙᴏʀʀᴀʀ", [
                    "Algunos archivos siguen en la nube.",
                    "El historial no se eliminó para no perder sus enlaces.",
                    "Inténtalo de nuevo en unos segundos.",
                ]),
                reply_markup=InlineKeyboardMarkup([
                    [btn("🧹 ʀᴇɪɴᴛᴇɴᴛᴀʀ", callback_data="history:clear:confirm")],
                    [btn("🏠 ɪɴɪᴄɪᴏ", callback_data="home")],
                ]),
            )
            return
        clear_task_history(user.id)
        schedule_data_backup(context.application, "borrado total del historial")
        await edit_query_message_text(query.message,
            card("✅ ʜɪꜱᴛᴏʀɪᴀʟ ʙᴏʀʀᴀᴅᴏ", ["El historial ha sido borrado con éxito."]),
            reply_markup=home_keyboard(),
        )
        return

    if query.data == "history:back":
        await edit_query_message_text(query.message,
            card("📜 ʜɪꜱᴛᴏʀɪᴀʟ", ["Vuelve a abrir /historial", "para ver tus registros."]),
            reply_markup=home_keyboard(),
        )
        return

    if query.data.startswith("j:"):
        try:
            _, task_id, action = query.data.split(":", 2)
        except ValueError:
            return
        manager: FairTaskQueue = context.application.bot_data["task_queue"]
        job = manager.jobs.get(task_id)
        if not job or job.user_id != user.id:
            await query.answer(sc("Tarea no disponible"), show_alert=True)
            return
        if action == "open":
            await show_task(job, target=query.message)
        elif action == "generate_txt":
            if job.state != "done" or not job.part_urls:
                return
            if job.txt_generating:
                return
            job.txt_generating = True
            txt_path = None
            try:
                txt_name = link_txt_filename(job.filename)
                urls = [url for _, url in sorted(job.part_urls.items())]
                content = "\n".join(urls) + "\n"
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
                    txt_path = Path(handle.name)
                    handle.write(content)
                with txt_path.open("rb") as document:
                    await context.bot.send_document(
                        chat_id=job.chat_id,
                        document=document,
                        filename=txt_name,
                        caption=sc("Aquí tienes el TXT con el enlace del archivo."),
                    )
            except Exception:
                log.exception("No se pudo generar TXT task=%s", job.task_id)
                await query.message.reply_text(sc("No se pudo generar el TXT. Inténtalo otra vez."))
            finally:
                job.txt_generating = False
                if txt_path:
                    txt_path.unlink(missing_ok=True)
        elif action == "cancel":
            cancelled = await manager.cancel(task_id, user.id)
            if cancelled:
                await _edit_task_status(job, task_detail_text(job))
            else:
                await query.answer(sc("Ya no se puede cancelar"), show_alert=True)
        elif action == "retry":
            if job.state not in {"error", "cancelled"}:
                await query.answer(sc("No necesita reintento"), show_alert=True)
            else:
                await manager.requeue(job)
                await _edit_task_status(job, card("🔄 ʀᴇᴀɴᴜᴅᴀɴᴅᴏ", ["Reanudando partes..."]))
        elif action == "links":
            await send_task_links(job, context.bot)
        elif action in {"copy", "copy_all"}:
            # Teclados antiguos: la acción de copia se migró a copy_text nativo.
            return
        elif action == "cloud_delete":
            if job.state != "done":
                await query.answer(sc("Solo archivos subidos"), show_alert=True)
                return
            urls = list(job.part_urls.items())
            results = await asyncio.gather(*(
                asyncio.to_thread(delete_from_todus_s3, url)
                for _, url in urls
            ), return_exceptions=True)
            failed = [
                (index, result) for (index, _), result in zip(urls, results)
                if isinstance(result, Exception)
            ]
            if failed:
                for (index, _), result in zip(urls, results):
                    if not isinstance(result, Exception):
                        job.part_urls.pop(index, None)
                await query.answer(sc("No se pudieron borrar todas las partes; inténtalo otra vez."), show_alert=True)
                return
            job.part_urls.clear()
            job.set_state("cancelled")
            job.error = "Borrado por el usuario"
            delete_task_history_by_task_id(user.id, job.task_id)
            schedule_data_backup(context.application, "borrado de archivo")
            await edit_query_message_text(query.message,
                card("🗑️ ᴀʀᴄʜɪᴠᴏ ᴇʟɪᴍɪɴᴀᴅᴏ", [
                    "El archivo fue eliminado",
                    "correctamente de la nube.",
                    "",
                    "También se eliminaron todas sus partes.",
                ]),
                reply_markup=home_keyboard(),
            )
        elif action == "delete":
            deleted = await manager.delete(task_id, user.id)
            if not deleted:
                await query.answer(sc("Primero cancela o espera"), show_alert=True)
            else:
                if deleted.temp_path:
                    Path(deleted.temp_path).unlink(missing_ok=True)
                for path in deleted.part_paths:
                    Path(path).unlink(missing_ok=True)
                if deleted.part_paths and Path(deleted.part_paths[0]).parent.name == f"jk-task-{deleted.task_id}":
                    shutil.rmtree(Path(deleted.part_paths[0]).parent, ignore_errors=True)
                await query.message.delete()
        return

    if query.data == "support":
        owner_id = parse_owner_id()
        if not owner_id:
            await edit_query_message_text(query.message,
                card("💬 ꜱᴏᴘᴏʀᴛᴇ", ["No disponible actualmente."]),
                reply_markup=main_keyboard(user.id),
            )
            return
        context.user_data["awaiting_support"] = True
        await edit_query_message_text(query.message,
            card("💬 ꜱᴏᴘᴏʀᴛᴇ", [
                "Escribe tu mensaje y",
                "se lo enviaré directo",
                "al administrador.",
            ]),
            reply_markup=contact_keyboard(),
        )
    elif query.data == "home":
        context.user_data.pop("admin_state", None)
        context.user_data.pop("awaiting_support", None)
        await edit_welcome_message(
            query.message,
            user.id,
            getattr(user, "full_name", "") or getattr(user, "first_name", ""),
            getattr(user, "username", None),
            main_keyboard(user.id),
        )


async def owner_reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_update(update):
        return
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not is_owner(user.id) or update.effective_chat.type != "private":
        if message:
            await message.reply_text(sc("Comando no disponible."))
        return
    if len(context.args) < 2:
        await message.reply_text(sc("Uso: /reply ID respuesta"))
        return
    try:
        target = int(context.args[0])
        if target <= 0:
            raise ValueError
    except ValueError:
        await message.reply_text(sc("El ID debe ser numérico."))
        return
    response = " ".join(context.args[1:])[:3500]
    try:
        await context.bot.send_message(
            chat_id=target,
            text=card("📩 ʀᴇꜱᴘᴜᴇꜱᴛᴀ ᴅᴇ ꜱᴏᴘᴏʀᴛᴇ", [response]),
        )
        await message.reply_text(sc("✅ Mensaje enviado."))
    except Exception:
        log.exception("No se pudo responder a %s", target)
        await message.reply_text(sc("❌ No se pudo enviar el mensaje."))


# ═══════════════════════════════════════════════════════════════
#  MANEJADORES DE TEXTO Y MULTIMEDIA
# ═══════════════════════════════════════════════════════════════
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_update(update):
        return
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    if not await ensure_membership(update, context):
        return
    if chat.type != "private":
        await message.reply_text(sc("Usa el chat privado del bot."))
        return

    text = (message.text or "").strip()

    admin_state = context.user_data.get("admin_state")
    if admin_state and is_owner(user.id):
        step = admin_state.get("step")

        if step == "admin_cloud_user":
            context.user_data.pop("admin_state", None)
            try:
                target_id = int(text)
                if target_id <= 0:
                    raise ValueError
            except ValueError:
                await message.reply_text(sc("Envía un ID numérico positivo."))
                return
            await message.reply_text(
                admin_user_history_text(target_id),
                reply_markup=admin_user_history_keyboard(target_id),
            )
            return

        if step in {"admin_block_user", "admin_unblock_user"}:
            context.user_data.pop("admin_state", None)
            try:
                target_id = int(text)
                if target_id <= 0:
                    raise ValueError
            except ValueError:
                await message.reply_text(sc("Envía un ID numérico positivo."))
                return
            if is_owner(target_id):
                await message.reply_text(sc("El creador no puede bloquearse ni desbloquearse."))
                return
            should_block = step == "admin_block_user"
            changed = set_user_blocked(target_id, should_block)
            schedule_data_backup(context.application, "bloqueo de usuario" if should_block else "desbloqueo de usuario")
            if should_block:
                try:
                    await context.bot.send_message(chat_id=target_id, text=blocked_user_text())
                except Exception:
                    log.info("No se pudo notificar el bloqueo al usuario %s", target_id)
            action = "bloqueado" if should_block else "desbloqueado"
            detail = "El usuario ya tenía ese estado." if not changed else f"El usuario quedó {action} correctamente."
            await message.reply_text(
                card("🛡️ ᴀᴄᴄᴇꜱᴏ ᴀᴄᴛᴜᴀʟɪᴢᴀᴅᴏ", [f"ID: {{mono:{target_id}}}", detail]),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if step == "awaiting_balance_user_id":
            try:
                target_id = int(text)
                if target_id <= 0:
                    raise ValueError
            except ValueError:
                await message.reply_text(sc("Envía un ID numérico positivo."))
                return
            context.user_data["admin_state"] = {
                "step": "awaiting_balance_amount",
                "target_id": target_id,
            }
            await message.reply_text(
                card("💵 ᴍᴏɴᴛᴏ ᴅᴇ ꜱᴀʟᴅᴏ", [
                    f"ID: {{mono:{target_id}}}",
                    "Envía ahora la cantidad",
                    "entera de pesos a acreditar.",
                    "",
                    "Ejemplo: 250",
                ]),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if step == "awaiting_balance_amount":
            try:
                amount = int(text)
                if amount <= 0 or amount > 1_000_000:
                    raise ValueError
            except ValueError:
                await message.reply_text(sc("Envía una cantidad entera entre 1 y 1000000."))
                return
            target_id = int(admin_state["target_id"])
            context.user_data["admin_state"] = {
                "step": "awaiting_balance_confirmation",
                "target_id": target_id,
                "amount": amount,
            }
            await message.reply_text(
                card("⚠️ ᴄᴏɴꜰɪʀᴍᴀʀ ꜱᴀʟᴅᴏ", [
                    f"ID: {{mono:{target_id}}}",
                    f"Cantidad: {amount}$",
                    "",
                    "¿Confirmas esta acreditación?",
                ]),
                reply_markup=InlineKeyboardMarkup([
                    [btn("✅ ᴄᴏɴꜰɪʀᴍᴀʀ", callback_data=f"admin:confirm_balance:{target_id}:{amount}")],
                    [btn("❌ ᴄᴀɴᴄᴇʟᴀʀ", callback_data="admin:back")],
                ]),
            )
            return

        if step == "admin_history_user":
            context.user_data.pop("admin_state", None)
            try:
                target_id = int(text)
                if target_id <= 0:
                    raise ValueError
            except ValueError:
                await message.reply_text(sc("Envía un ID numérico positivo."))
                return
            await message.reply_text(
                admin_user_history_text(target_id),
                reply_markup=admin_user_history_keyboard(target_id),
            )
            return

        if step == "broadcast_message":
            context.user_data.pop("admin_state", None)
            body = text[:3500]
            if not body:
                await message.reply_text(sc("El mensaje no puede estar vacío."))
                return
            total, sent, failed = await broadcast_admin_message(context.bot, body)
            await message.reply_text(
                card("📢 ᴅɪꜰᴜꜱɪóɴ ᴛᴇʀᴍɪɴᴀᴅᴀ", [
                    f"Registrados: {total}",
                    f"Entregados: {sent}",
                    f"No entregados: {failed}",
                ]),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if step == "check_user":
            context.user_data.pop("admin_state", None)
            try:
                target_id = int(text)
            except ValueError:
                await message.reply_text(
                    card("❌ ᴇʀʀᴏʀ", ["Envía un ID numérico válido."]),
                    reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
                )
                return
            plan_key = get_user_plan(target_id)
            plan = get_plan_info(plan_key)
            used = get_daily_usage(target_id)
            exp_date, rem_days = get_plan_expiry_info(target_id)
            lines = [
                f"🆔 ID: {{mono:{target_id}}}",
                f"🏷 Plan: {plan['badge']} {plan['display']}",
                f"📦 Cuota: {plan['daily_label']}",
                f"📊 Usado: {used / GIB:.2f} GB",
            ]
            if plan['daily_bytes'] != float('inf'):
                lines.append(f"💾 Libre: {max(0, plan['daily_bytes'] - used) / GIB:.2f} GB")
            else:
                lines.append(f"💾 Libre: ∞ GB")
            if exp_date:
                lines.append(f"📅 Vence: {exp_date}")
            if rem_days is not None:
                lines.append(f"⏳ Restan: {rem_days} días")
            await message.reply_text(
                card("🔍 ɪɴꜰᴏ ᴜꜱᴜᴀʀɪᴏ", lines),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if step == "awaiting_custom_days":
            try:
                days = int(text)
                if days <= 0:
                    raise ValueError
            except ValueError:
                await message.reply_text(
                    card("❌ ᴇʀʀᴏʀ", ["Ingresa un número de días mayor a 0."]),
                    reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
                )
                return
            plan_key = admin_state["plan"]
            context.user_data["admin_state"] = {"step": "awaiting_user_id", "plan": plan_key, "days": days}
            plan = get_plan_info(plan_key)
            lines = [
                f"Plan: {plan['badge']} {plan['display']}",
                f"Duración: {days} días",
                "",
                "Envía ahora el ID",
                "de Telegram del",
                "usuario.",
            ]
            await message.reply_text(
                card("🆔 ɪɴɢʀᴇꜱᴀʀ ᴜꜱᴜᴀʀɪᴏ", lines),
                reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
            )
            return

        if step == "awaiting_user_id":
            try:
                target_id = int(text)
                if target_id <= 0:
                    raise ValueError
            except ValueError:
                await message.reply_text(
                    card("❌ ᴇʀʀᴏʀ", ["Envía un ID numérico válido."]),
                    reply_markup=InlineKeyboardMarkup([[btn("⬅️ ᴘᴀɴᴇʟ ᴀᴅᴍɪɴ", callback_data="admin:back")]]),
                )
                return
            plan_key = admin_state["plan"]
            days = admin_state.get("days", 0)
            plan = get_plan_info(plan_key)
            lines = [
                f"Usuario: {{mono:{target_id}}}",
                f"Plan: {plan['badge']} {plan['display']}",
                f"Duración: {f'{days} días' if days > 0 else 'Permanente'}",
                f"Cuota: {plan['daily_label']}/día",
                "",
                "¿Confirmar asignación?",
            ]
            await message.reply_text(
                card("✅ ᴄᴏɴꜰɪʀᴍᴀʀ", lines),
                reply_markup=InlineKeyboardMarkup([
                    [btn("✅ ᴄᴏɴꜰɪʀᴍᴀʀ", callback_data=f"admin:confirm_plan:{plan_key}:{days}:{target_id}")],
                    [btn("❌ ᴄᴀɴᴄᴇʟᴀʀ", callback_data="admin:back")],
                ]),
            )
            return

    if context.user_data.get("awaiting_support"):
        owner_id = parse_owner_id()
        if not owner_id:
            context.user_data.pop("awaiting_support", None)
            await message.reply_text(sc("Soporte no disponible."))
            return
        body = text[:3000]
        username = f"@{user.username}" if user.username else "(sin alias)"
        try:
            await context.bot.send_message(
                chat_id=owner_id,
                text=(
                    f"📩 Nuevo mensaje de soporte\n"
                    f"👤 De: {username}\n"
                    f"🆔 ID: <code>{user.id}</code>\n\n"
                    f"{body}\n\n"
                    f"👉 Responde con:\n<code>/reply {user.id} tu respuesta</code>"
                ),
                parse_mode="HTML",
            )
            context.user_data.pop("awaiting_support", None)
            await message.reply_text(
                card("✅ ᴇɴᴠɪᴀᴅᴏ", ["Tu mensaje ha sido", "enviado al admin."]),
                reply_markup=home_keyboard(),
            )
        except Exception:
            log.exception("Error al enviar soporte de user=%s", user.id)
            await message.reply_text(sc("No se pudo enviar."))
        return

    allowed: set[int] = context.application.bot_data["allowed_users"]
    if allowed and user.id not in allowed and not is_owner(user.id):
        await message.reply_text(sc("Bot limitado a usuarios autorizados."))
        return

    try:
        source_urls = extract_http_urls(text)
    except ValueError as exc:
        await message.reply_text(sc(str(exc)))
        return
    for source_url in source_urls:
        await enqueue_transfer(update, context, source_kind="url", source_url=source_url)


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_update(update):
        return
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    if not await ensure_membership(update, context):
        return
    if chat.type != "private":
        await message.reply_text(sc("Usa el chat privado del bot."))
        return
    allowed: set[int] = context.application.bot_data["allowed_users"]
    if allowed and user.id not in allowed and not is_owner(user.id):
        await message.reply_text(sc("Bot limitado a usuarios autorizados."))
        return
    size_hint = None
    for media_name in ("document", "video", "animation", "audio", "voice", "video_note"):
        media = getattr(message, media_name, None)
        if media:
            size_hint = getattr(media, "file_size", None)
            break
    if not size_hint and getattr(message, "photo", None):
        size_hint = getattr(message.photo[-1], "file_size", None)
    max_size = env_int("MAX_FILE_SIZE_MIB", 4096) * MIB
    if size_hint and size_hint > max_size and not is_owner(user.id):
        await message.reply_text(sc(f"El archivo supera el límite de {_size_label(max_size, 0)}."))
        return
    if size_hint and not is_owner(user.id):
        ok, err_msg = check_quota(user.id, size_hint)
        if not ok:
            await message.reply_text(err_msg, reply_markup=quota_exhausted_keyboard())
            return
    await enqueue_transfer(update, context, source_kind="media")


# ═══════════════════════════════════════════════════════════════
#  INICIALIZACIÓN Y ARRANQUE DEL BOT
# ═══════════════════════════════════════════════════════════════
def schedule_data_backup(application: Application, reason: str = "cambio de datos") -> None:
    """Programa una única copia cifrada para el último cambio de estado."""
    backup_chat_id = configured_backup_chat_id()
    layout = application.bot_data.get("telegram_backup_layout")
    current = application.bot_data.get("telegram_backup_pending_task")
    if not backup_chat_id or layout is None:
        return
    async def run() -> None:
        lock = application.bot_data.get("telegram_backup_lock")
        if lock is None:
            lock = asyncio.Lock()
            application.bot_data["telegram_backup_lock"] = lock
        async with lock:
            try:
                await backup_to_telegram(
                    application.bot,
                    getattr(application.bot, "token", ""),
                    backup_chat_id,
                    layout,
                    logger=log,
                )
                log.info("Respaldo automático completado: %s", reason)
            except Exception as exc:
                log.warning("Respaldo automático omitido (%s, %s).", reason, type(exc).__name__)
    application.bot_data["telegram_backup_pending_task"] = asyncio.create_task(run())


async def post_init(application: Application) -> None:
    backup_chat_id = configured_backup_chat_id()
    backup_layout = telegram_backup_layout()
    backup_layout.auth_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        backup_layout.auth_dir.chmod(0o700)
    except OSError:
        pass
    application.bot_data["telegram_backup_layout"] = backup_layout
    application.bot_data["telegram_backup_lock"] = asyncio.Lock()
    log.info("Ruta de autenticación Telegram: %s", backup_layout.auth_dir)
    if not backup_chat_id:
        log.error("No hay destino de respaldo: configura TELEGRAM_BACKUP_CHAT_ID.")
    else:
        log.info("Destino de respaldo configurado: %s", backup_chat_id)

    # Restaurar datos antes de abrir la sesión MTProto de Telegram.
    if backup_chat_id:
        try:
            restored_files, restored_bytes = await restore_from_telegram(
                application.bot,
                getattr(application.bot, "token", ""),
                backup_chat_id,
                backup_layout,
                logger=log,
            )
            log.info("Restauración inicial terminada: archivos=%d bytes=%d", restored_files, restored_bytes)
        except Exception as exc:
            log.warning("Se continúa sin restaurar la copia Telegram (%s).", type(exc).__name__)
        for attempt in range(1, 4):
            try:
                await backup_to_telegram(
                    application.bot,
                    getattr(application.bot, "token", ""),
                    backup_chat_id,
                    backup_layout,
                    logger=log,
                )
                log.info("Copia inicial publicada y fijada en el intento %d.", attempt)
                break
            except Exception as exc:
                # Un deploy puede arrancar mientras Telegram aún no responde.
                log.warning("No se creó la copia inicial (intento %d/3, %s).", attempt, type(exc).__name__)
                if attempt < 3:
                    await asyncio.sleep(5)
        backup_interval = env_int("TELEGRAM_BACKUP_INTERVAL_SECONDS", 300, minimum=30)
        application.bot_data["telegram_backup_task"] = asyncio.create_task(
            periodic_backup(
                application.bot,
                getattr(application.bot, "token", ""),
                backup_chat_id,
                backup_layout,
                interval_seconds=backup_interval,
                lock=application.bot_data["telegram_backup_lock"],
                logger=log,
            )
        )

    identity = await application.bot.get_me()
    application.bot_data["bot_username"] = identity.username or os.getenv("BOT_USERNAME", "jk_down_bot")
    await asyncio.to_thread(expire_cloud_files)
    application.bot_data["cloud_expiration_task"] = asyncio.create_task(cloud_expiration_monitor(application))
    log.info("Bot activo: @%s", identity.username or "sin_username")
    log.info(f"📂 Directorio de datos: {DATA_DIR}")
    log.info(f"📄 Planes: {PLAN_DATA_FILE}")
    log.info(f"📊 Cuotas: {USAGE_DATA_FILE}")
    log.info(f"📜 Historial: {HISTORY_DATA_FILE}")
    await application.bot.set_my_commands([
        BotCommand("start", sc("abrir menú")),
        BotCommand("descargas", sc("menú de descargas")),
        BotCommand("ayuda", sc("ver ayuda")),
        BotCommand("cola", sc("tareas en curso")),
        BotCommand("historial", sc("historial de descargas")),
        BotCommand("micuota", sc("ver mi plan y cuota")),
        BotCommand("id", sc("ver mi id numérico")),
        BotCommand("backup", sc("probar respaldo")),
    ])
    log.info("Comandos del bot registrados correctamente")
    if backup_chat_id:
        try:
            await backup_to_telegram(
                application.bot,
                getattr(application.bot, "token", ""),
                backup_chat_id,
                backup_layout,
                logger=log,
            )
            log.info("Respaldo confirmado después de conectar el bot.")
        except Exception as exc:
            log.warning("No se pudo confirmar el respaldo después de conectar (%s).", type(exc).__name__)
    if application.bot_data.get("task_queue"):
        workers = application.bot_data.get("task_worker_count", 1)
        application.bot_data["task_workers"] = [
            asyncio.create_task(task_worker(application)) for _ in range(workers)
        ]
    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    application.bot_data["mtproto_client"] = None
    if not api_id_raw and not api_hash:
        log.info("MTProto opcional deshabilitado; el bot sigue activo. Configura TELEGRAM_API_ID y TELEGRAM_API_HASH solo para descargas grandes.")
        return
    if not api_id_raw or not api_hash:
        log.info("MTProto opcional deshabilitado: TELEGRAM_API_ID y TELEGRAM_API_HASH deben configurarse juntos; el bot sigue activo.")
        return
    mtproto_client = None
    try:
        session_path = TELEGRAM_AUTH_DIR / "mtproto_bot.session"
        mtproto_client = TelegramClient(str(session_path), int(api_id_raw), api_hash)
        await mtproto_client.start(bot_token=application.bot.token)
        try:
            session_path.chmod(0o600)
        except OSError:
            pass
        application.bot_data["mtproto_client"] = mtproto_client
        application.bot_data["mtproto_message_cache"] = {}

        async def cache_media_update(event):
            if not getattr(event.message, "media", None):
                return
            cache = application.bot_data["mtproto_message_cache"]
            cache[(event.chat_id, event.id)] = event.message
            if len(cache) > 128:
                cache.pop(next(iter(cache)))

        mtproto_client.add_event_handler(cache_media_update, events.NewMessage)
        try:
            await mtproto_client.catch_up()
        except Exception as exc:
            log.warning("MTProto sync (%s)", type(exc).__name__)
        log.info("MTProto listo")
        # El respaldo anterior al arranque garantiza restauración previa; esta copia
        # posterior captura también la sesión SQLite que MTProto acaba de abrir.
        if backup_chat_id:
            try:
                await backup_to_telegram(
                    application.bot,
                    getattr(application.bot, "token", ""),
                    backup_chat_id,
                    backup_layout,
                    logger=log,
                )
            except Exception as exc:
                log.warning("No se actualizó la copia tras iniciar MTProto (%s).", type(exc).__name__)
    except Exception as exc:
        if mtproto_client:
            try:
                await mtproto_client.disconnect()
            except Exception:
                pass
        application.bot_data["mtproto_client"] = None
        application.bot_data.pop("mtproto_message_cache", None)
        log.error("MTProto no activado (%s)", type(exc).__name__)


async def post_shutdown(application: Application) -> None:
    expiration_task = application.bot_data.get("cloud_expiration_task")
    if expiration_task:
        expiration_task.cancel()
        await asyncio.gather(expiration_task, return_exceptions=True)
    backup_task = application.bot_data.get("telegram_backup_task")
    if backup_task:
        backup_task.cancel()
        await asyncio.gather(backup_task, return_exceptions=True)
    pending_backup = application.bot_data.get("telegram_backup_pending_task")
    if pending_backup:
        await asyncio.gather(pending_backup, return_exceptions=True)
    workers = application.bot_data.get("task_workers", [])
    for worker in workers:
        worker.cancel()
    if workers:
        await asyncio.gather(*workers, return_exceptions=True)
    mtproto_client = application.bot_data.get("mtproto_client")
    if mtproto_client is not None and mtproto_client.is_connected():
        await mtproto_client.disconnect()
    backup_layout = application.bot_data.get("telegram_backup_layout")
    backup_chat_id = configured_backup_chat_id()
    if backup_layout and backup_chat_id:
        try:
            await backup_to_telegram(
                application.bot,
                getattr(application.bot, "token", ""),
                backup_chat_id,
                backup_layout,
                logger=log,
            )
        except Exception as exc:
            log.warning("No se completó el respaldo al cerrar (%s).", type(exc).__name__)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or TELEGRAM_BOT_TOKEN.strip()
    if not token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en el entorno o archivo")
    temp_dir = os.getenv("TMPDIR", "/tmp").strip() or "/tmp"
    Path(temp_dir).mkdir(parents=True, exist_ok=True)

    api_base = os.getenv("TELEGRAM_API_BASE_URL", "").strip().rstrip("/")
    file_base = os.getenv("TELEGRAM_FILE_BASE_URL", "").strip().rstrip("/")
    if bool(api_base) != bool(file_base):
        raise RuntimeError("Configura juntos TELEGRAM_API_BASE_URL y TELEGRAM_FILE_BASE_URL")

    # ⚡ Habilitar HTML por defecto para que <code>...</code> funcione
    defaults = Defaults(parse_mode=ParseMode.HTML)
    builder = Application.builder().token(token).defaults(defaults).post_init(post_init).post_shutdown(post_shutdown)
    if api_base:
        builder = builder.base_url(api_base).base_file_url(file_base)
    application = builder.build()
    application.bot_data.update({
        "mtproto_client": None,
        "local_bot_api": bool(api_base),
        "owner_id": parse_owner_id(),
        "allowed_users": parse_allowed_users(),
        "task_queue": FairTaskQueue(),
        "task_worker_count": env_int("MAX_CONCURRENT_UPLOADS", 2),
        "per_user_task_limit": env_int("MAX_PENDING_PER_USER", 25),
    })
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("descargas", downloads_command))
    application.add_handler(CommandHandler("ayuda", help_command))
    application.add_handler(CommandHandler("cola", queue_command))
    application.add_handler(CommandHandler("historial", history_command))
    application.add_handler(CommandHandler("micuota", my_quota_command))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("top", top_command))
    application.add_handler(CommandHandler("backup", backup_command))
    application.add_handler(CommandHandler("jkadmin", jkadmin_command))
    application.add_handler(CommandHandler("reply", owner_reply_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    media_filters = (
        filters.Document.ALL | filters.VIDEO | filters.AUDIO
        | filters.PHOTO | filters.ANIMATION | filters.VIDEO_NOTE | filters.VOICE
    )
    application.add_handler(MessageHandler(media_filters, handle_media))
    log.info("JK bot completamente inicializado y listo")
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
