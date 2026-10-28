from __future__ import annotations

import asyncio
import base64
import binascii
import gzip
import hashlib
import hmac
import io
import json
import logging
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT_NAME = "jk-telegram-bot-backup"
FORMAT_VERSION = 1
BACKUP_FILENAME = "jk-telegram-backup-v1.enc"
BACKUP_CAPTION = "ᴊᴋ ʙᴏᴛ ᴜᴘᴅᴀᴛᴇꜱ"
MAGIC = b"JKTGBAK1"
KEY_PREFIX = b"jk-telegram-bot-backup-v1\x00"
NONCE_BYTES = 12
# Bot API alojada: getFile solo permite descargar hasta 20 MiB.
MAX_BACKUP_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_SNAPSHOT_JSON_BYTES = 32 * 1024 * 1024
STATE_KEYS = (
    "user_plans.json",
    "daily_usage.json",
    "task_history.json",
    "users.json",
    "admin_audit.json",
    "todus_users.json",
    "todus_auth.json",
)
OLD_BACKUP_DIR_NAMES = {"backup", "backups", "old_backup", "old_backups", "archive", "archives"}


def build_backup_caption(
    snapshot: dict[str, Any],
    total_bytes: int,
    file_count: int,
    *,
    action: str = "ᴀᴄᴛᴜᴀʟɪᴢᴀᴄɪóɴ",
) -> str:
    """Crea un resumen legible de la actualización sin exponer el contenido cifrado."""
    try:
        cuba_now = datetime.now(ZoneInfo("America/Havana"))
    except Exception:
        cuba_now = datetime.now(timezone.utc)
    timestamp = cuba_now.strftime("%Y-%m-%d %H:%M CUBA")
    if total_bytes >= 1_000_000_000:
        size_label = f"{total_bytes / 1_000_000_000:.2f} GB"
    else:
        size_label = f"{total_bytes / 1_000_000:.2f} MB"
    width = 22

    def line(text: str) -> str:
        return f"┃ {text:<{width}}"

    return "\n".join([
        "╭" + "━" * width + "╮",
        line("ᴊᴋ ʙᴏᴛ ᴜᴘᴅᴀᴛᴇꜱ"),
        "├" + "─" * width + "┤",
        line("ᴇꜱᴛᴀᴅᴏ: ᴄɪꜰʀᴀᴅᴏ"),
        line(f"ᴀᴄᴄɪóɴ: {action}"),
        line(f"ʜᴏʀᴀ: {timestamp}"),
        line(f"ᴀʀᴄʜɪᴠᴏꜱ: {file_count}"),
        line(f"ᴛᴀᴍᴀñᴏ: {size_label}"),
        line(f"ᴠᴇʀꜱɪóɴ: {snapshot.get('version', FORMAT_VERSION)}"),
        "╰" + "━" * width + "╯",
    ])


class BackupError(RuntimeError):
    """Error seguro de respaldo, sin datos sensibles ni credenciales."""


class BackupSizeError(BackupError):
    pass


class BackupFormatError(BackupError):
    pass


@dataclass(frozen=True)
class BackupLayout:
    data_dir: Path
    auth_dir: Path
    state_files: dict[str, Path]

    @classmethod
    def from_paths(
        cls,
        data_dir: str | Path,
        auth_dir: str | Path,
        state_files: dict[str, str | Path],
    ) -> "BackupLayout":
        unknown = set(state_files) - set(STATE_KEYS)
        if unknown:
            raise ValueError("state_files contiene una clave desconocida")
        return cls(
            data_dir=Path(data_dir),
            auth_dir=Path(auth_dir),
            state_files={key: Path(value) for key, value in state_files.items()},
        )


def derive_backup_key(bot_token: str) -> bytes:
    token = (bot_token or "").strip()
    if not token:
        raise BackupError("Falta TELEGRAM_BOT_TOKEN para cifrar o descifrar la copia.")
    return hmac.new(token.encode("utf-8"), KEY_PREFIX, hashlib.sha256).digest()


def _validate_relative_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise BackupFormatError("La copia contiene una ruta no válida.")
    rel = PurePosixPath(value)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise BackupFormatError("La copia contiene una ruta no segura.")
    return rel


def _decode_entry(entry: dict[str, Any]) -> bytes:
    if not isinstance(entry, dict):
        raise BackupFormatError("La copia contiene un archivo no válido.")
    for field in ("path", "kind", "size", "sha256", "data_b64"):
        if field not in entry:
            raise BackupFormatError("La copia está incompleta.")
    rel = _validate_relative_path(entry["path"])
    kind = entry["kind"]
    if kind == "state":
        if len(rel.parts) != 2 or rel.parts[0] != "state" or rel.parts[1] not in STATE_KEYS:
            raise BackupFormatError("La copia contiene una ruta de estado no reconocida.")
    elif kind == "auth":
        if len(rel.parts) < 2 or rel.parts[0] != "telegram_auth":
            raise BackupFormatError("La copia contiene una ruta de sesión no reconocida.")
    else:
        raise BackupFormatError("La copia contiene un tipo de archivo no reconocido.")
    try:
        raw = base64.b64decode(entry["data_b64"], validate=True)
    except (ValueError, binascii.Error, TypeError) as exc:
        raise BackupFormatError("La copia contiene datos codificados no válidos.") from exc
    if not isinstance(entry["size"], int) or entry["size"] < 0 or len(raw) != entry["size"]:
        raise BackupFormatError("El tamaño de un archivo no coincide con el manifiesto.")
    if not isinstance(entry["sha256"], str) or not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), entry["sha256"]
    ):
        raise BackupFormatError("La integridad de un archivo no coincide con el manifiesto.")
    if kind == "state":
        try:
            decoded_state = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupFormatError("Un JSON de estado está incompleto o no es válido.") from exc
        if not isinstance(decoded_state, dict):
            raise BackupFormatError("Un JSON de estado no tiene el formato esperado.")
    return raw


def validate_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise BackupFormatError("La instantánea no es un objeto JSON.")
    if snapshot.get("format") != FORMAT_NAME or snapshot.get("version") != FORMAT_VERSION:
        raise BackupFormatError("La versión de la copia no es compatible.")
    if not isinstance(snapshot.get("created_at"), str) or not snapshot["created_at"]:
        raise BackupFormatError("La copia está incompleta: falta la fecha.")
    entries = snapshot.get("files")
    if not isinstance(entries, list):
        raise BackupFormatError("La copia está incompleta: falta la lista de archivos.")
    seen: set[str] = set()
    total = 0
    for entry in entries:
        raw = _decode_entry(entry)
        if entry["path"] in seen:
            raise BackupFormatError("La copia contiene rutas duplicadas.")
        seen.add(entry["path"])
        total += len(raw)
        if total > MAX_BACKUP_DOCUMENT_BYTES:
            raise BackupSizeError(
                f"La copia contiene {total} bytes sin comprimir; el máximo transferible es "
                f"{MAX_BACKUP_DOCUMENT_BYTES} bytes."
            )
    return snapshot


def _snapshot_total_bytes(snapshot: dict[str, Any]) -> int:
    return sum(int(entry.get("size", 0)) for entry in snapshot.get("files", []))


def _previous_state_bytes(previous_snapshot: dict[str, Any] | None, key: str) -> bytes | None:
    if not previous_snapshot:
        return None
    target = f"state/{key}"
    for entry in previous_snapshot.get("files", []):
        if entry.get("path") == target and entry.get("kind") == "state":
            return _decode_entry(entry)
    return None


def _merge_state_content(key: str, current: bytes, previous_snapshot: dict[str, Any] | None) -> bytes:
    """Devuelve únicamente el estado actual; las eliminaciones deben sobrevivir al deploy."""
    return current

def encrypt_snapshot(snapshot: dict[str, Any], bot_token: str) -> bytes:
    validate_snapshot(snapshot)
    raw_json = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw_json) > MAX_SNAPSHOT_JSON_BYTES:
        raise BackupSizeError(
            f"La instantánea JSON ocupa {len(raw_json)} bytes; el máximo permitido es "
            f"{MAX_SNAPSHOT_JSON_BYTES} bytes."
        )
    compressed = gzip.compress(raw_json, mtime=0)
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(derive_backup_key(bot_token)).encrypt(nonce, compressed, MAGIC)
    result = MAGIC + nonce + ciphertext
    if len(result) > MAX_BACKUP_DOCUMENT_BYTES:
        raise BackupSizeError(
            f"La copia cifrada ocupa {len(result)} bytes; Telegram permite descargar como máximo "
            f"{MAX_BACKUP_DOCUMENT_BYTES} bytes con getFile."
        )
    return result


def decrypt_snapshot(encrypted: bytes, bot_token: str) -> dict[str, Any]:
    if not isinstance(encrypted, (bytes, bytearray)) or len(encrypted) > MAX_BACKUP_DOCUMENT_BYTES:
        actual = len(encrypted) if isinstance(encrypted, (bytes, bytearray)) else 0
        raise BackupSizeError(
            f"La copia descargada ocupa {actual} bytes; el máximo es {MAX_BACKUP_DOCUMENT_BYTES} bytes."
        )
    min_len = len(MAGIC) + NONCE_BYTES + 16
    if len(encrypted) < min_len or not encrypted.startswith(MAGIC):
        raise BackupFormatError("El documento no es una copia cifrada compatible.")
    nonce_start = len(MAGIC)
    nonce = bytes(encrypted[nonce_start:nonce_start + NONCE_BYTES])
    ciphertext = bytes(encrypted[nonce_start + NONCE_BYTES:])
    try:
        compressed = AESGCM(derive_backup_key(bot_token)).decrypt(nonce, ciphertext, MAGIC)
    except Exception as exc:
        raise BackupFormatError("No se pudo autenticar la copia cifrada.") from exc
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
            raw_json = stream.read(MAX_SNAPSHOT_JSON_BYTES + 1)
        if len(raw_json) > MAX_SNAPSHOT_JSON_BYTES:
            raise BackupSizeError(
                f"La instantánea descomprimida supera {MAX_SNAPSHOT_JSON_BYTES} bytes."
            )
        snapshot = json.loads(raw_json.decode("utf-8"))
    except BackupError:
        raise
    except Exception as exc:
        raise BackupFormatError("El contenido comprimido de la copia está incompleto o dañado.") from exc
    return validate_snapshot(snapshot)


def _entry(path: str, kind: str, content: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "kind": kind,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "data_b64": base64.b64encode(content).decode("ascii"),
    }


def _read_regular_file_no_follow(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BackupError("No se pudo abrir un archivo regular de respaldo.") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BackupError("Se omitió un archivo que no es regular.")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _is_old_backup_component(name: str) -> bool:
    lower = name.lower()
    return lower in OLD_BACKUP_DIR_NAMES or lower.startswith(".backup") or lower.startswith("backup-")


def _walk_auth_files(auth_dir: Path):
    if not auth_dir.exists() or auth_dir.is_symlink():
        return
    for current, dirs, files in os.walk(auth_dir, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            name for name in dirs
            if not _is_old_backup_component(name)
            and not (current_path / name).is_symlink()
        ]
        for name in files:
            path = current_path / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                yield path


def build_snapshot(
    layout: BackupLayout,
    *,
    previous_snapshot: dict[str, Any] | None = None,
    max_total_bytes: int = MAX_BACKUP_DOCUMENT_BYTES,
) -> tuple[dict[str, Any], int, int]:
    entries: list[dict[str, Any]] = []
    local_auth_paths: set[str] = set()
    local_state_paths: set[str] = set()
    total_bytes = 0
    missing_state_keys: list[str] = []

    for key in STATE_KEYS:
        path = layout.state_files.get(key)
        if path is None:
            continue
        try:
            info = path.lstat()
        except FileNotFoundError:
            missing_state_keys.append(key)
            continue
        except OSError as exc:
            raise BackupError(f"No se pudo leer el archivo de estado {key}.") from exc
        if not stat.S_ISREG(info.st_mode):
            continue
        try:
            content = _read_regular_file_no_follow(path)
        except BackupError:
            continue
        try:
            decoded_state = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupFormatError(f"El JSON de estado {key} está incompleto o no es válido.") from exc
        if not isinstance(decoded_state, dict):
            raise BackupFormatError(f"El JSON de estado {key} no tiene el formato esperado.")
        total_bytes += len(content)
        if total_bytes > max_total_bytes:
            raise BackupSizeError(
                f"Los archivos de estado y sesión suman {total_bytes} bytes; el límite es {max_total_bytes} bytes."
            )
        entries.append(_entry(f"state/{key}", "state", content))
        local_state_paths.add(f"state/{key}")

    auth_dir = layout.auth_dir
    for path in _walk_auth_files(auth_dir):
        try:
            relative = path.relative_to(auth_dir).as_posix()
            content = _read_regular_file_no_follow(path)
        except (OSError, ValueError) as exc:
            raise BackupError("No se pudo leer un archivo de sesión de Telegram.") from exc
        archive_path = f"telegram_auth/{relative}"
        local_auth_paths.add(archive_path)
        total_bytes += len(content)
        if total_bytes > max_total_bytes:
            raise BackupSizeError(
                f"Los archivos de estado y sesión suman {total_bytes} bytes; el límite es {max_total_bytes} bytes."
            )
        entries.append(_entry(archive_path, "auth", content))

    # Un archivo de estado ausente puede ocurrir durante un deploy o una
    # restauración parcial. Nunca debe hacer que la nueva copia elimine esos
    # datos de la copia anterior. Los borrados intencionales sí se conservan,
    # porque el archivo existe y contiene {} o su estado actualizado.
    for key in missing_state_keys:
        content = _previous_state_bytes(previous_snapshot, key) or b"{}"
        total_bytes += len(content)
        if total_bytes > max_total_bytes:
            raise BackupSizeError(
                f"Los archivos de estado y sesión suman {total_bytes} bytes; el límite es {max_total_bytes} bytes."
            )
        entries.append(_entry(f"state/{key}", "state", content))
    if not entries:
        raise BackupError("La instantánea no contiene estado ni archivos de sesión; se conserva la copia anterior.")

    snapshot = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": entries,
    }
    validate_snapshot(snapshot)
    return snapshot, total_bytes, len(entries)


def build_encrypted_backup(
    layout: BackupLayout,
    bot_token: str,
    *,
    previous_snapshot: dict[str, Any] | None = None,
    max_total_bytes: int = MAX_BACKUP_DOCUMENT_BYTES,
) -> tuple[bytes, dict[str, Any], int, int]:
    snapshot, total_bytes, file_count = build_snapshot(
        layout,
        previous_snapshot=previous_snapshot,
        max_total_bytes=max_total_bytes,
    )
    encrypted = encrypt_snapshot(snapshot, bot_token)
    return encrypted, snapshot, total_bytes, file_count


def _secure_target(root: Path, relative: PurePosixPath) -> Path:
    if root.is_symlink():
        raise BackupFormatError("La carpeta local de sesión es un enlace simbólico.")
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    root_resolved = root.resolve()
    target = root.joinpath(*relative.parts)
    target_parent = root
    for part in relative.parts[:-1]:
        target_parent = target_parent / part
        if target_parent.is_symlink():
            raise BackupFormatError("La copia apunta a un directorio simbólico local.")
        target_parent.mkdir(exist_ok=True)
        try:
            target_parent.chmod(0o700)
        except OSError:
            pass
    resolved_parent = target_parent.resolve()
    try:
        resolved_parent.relative_to(root_resolved)
    except ValueError as exc:
        raise BackupFormatError("La copia intenta salir de la carpeta de sesión.") from exc
    if target.is_symlink():
        raise BackupFormatError("La copia apunta a un enlace simbólico local.")
    return target


def restore_snapshot(snapshot: dict[str, Any], layout: BackupLayout) -> tuple[int, int]:
    validate_snapshot(snapshot)
    restored = 0
    restored_bytes = 0
    for entry in snapshot["files"]:
        content = _decode_entry(entry)
        if entry["kind"] == "state":
            key = PurePosixPath(entry["path"]).name
            target = layout.state_files.get(key)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                continue
        else:
            relative = _validate_relative_path(entry["path"])
            target = _secure_target(layout.auth_dir, PurePosixPath(*relative.parts[1:]))
        if entry["kind"] == "state" and target.exists():
            # Un deploy puede crear un JSON vacío antes de que termine la
            # restauración. En ese caso sí debe entrar el estado respaldado;
            # un archivo local con datos se conserva para no pisar cambios.
            try:
                existing = target.read_bytes().strip()
                if existing not in {b"", b"{}"}:
                    continue
            except OSError:
                continue
            try:
                target.write_bytes(content)
                target.chmod(0o600)
                restored += 1
                restored_bytes += len(content)
            except OSError:
                continue
            continue
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(target, flags, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
        except FileExistsError:
            # Nunca se reemplaza una sesión/estado local ya existente.
            continue
        restored += 1
        restored_bytes += len(content)
    return restored, restored_bytes


def is_backup_message(message: Any) -> bool:
    document = getattr(message, "document", None)
    caption = str(getattr(message, "caption", "") or "")
    return bool(
        document
        and (
            getattr(document, "file_name", None) == BACKUP_FILENAME
            or caption.startswith("╭") and "ᴊᴋ ʙᴏᴛ ᴜᴘᴅᴀᴛᴇꜱ" in caption
        )
    )


def _message_pointer_path(layout: BackupLayout) -> Path:
    return layout.data_dir / ".telegram_backup_message_id"


def _read_message_pointer(layout: BackupLayout) -> int | None:
    try:
        value = _message_pointer_path(layout).read_text(encoding="utf-8").strip()
        return int(value) if value else None
    except (FileNotFoundError, OSError, ValueError):
        return None


def _write_message_pointer(layout: BackupLayout, message_id: int) -> None:
    try:
        path = _message_pointer_path(layout)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(str(message_id), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # La copia sigue siendo válida; solo se perderá la referencia local.
        pass


async def _download_message(bot: Any, message: Any, bot_token: str) -> dict[str, Any]:
    document = getattr(message, "document", None)
    if not document or not getattr(document, "file_id", None):
        raise BackupFormatError("El mensaje fijado no contiene una copia reconocida.")
    file = await bot.get_file(document.file_id)
    target = io.BytesIO()
    await file.download_to_memory(out=target)
    return decrypt_snapshot(target.getvalue(), bot_token)


def _parse_chat_id(value: str | int | None) -> int | str | None:
    if value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", text):
        return text
    if not re.fullmatch(r"-?[0-9]+", text):
        raise BackupError("TELEGRAM_BACKUP_CHAT_ID debe ser un ID numérico o @usuario de Telegram.")
    chat_id = int(text)
    if chat_id == 0:
        raise BackupError("TELEGRAM_BACKUP_CHAT_ID no puede ser cero.")
    return chat_id


def _pinned_backup(chat: Any) -> Any | None:
    message = getattr(chat, "pinned_message", None)
    return message if is_backup_message(message) else None


def _pinned_document_candidate(chat: Any) -> Any | None:
    """Devuelve un mensaje fijado con documento cuando Telegram omitió metadatos."""
    message = getattr(chat, "pinned_message", None)
    if message is not None and getattr(message, "message_id", None) and getattr(message, "document", None):
        return message
    return None


def _message_with_id(message_id: int | None) -> Any | None:
    """Crea una referencia mínima para editar un mensaje conocido por su ID."""
    if not message_id:
        return None
    return type("BackupMessageRef", (), {"message_id": message_id})()


async def restore_from_telegram(
    bot: Any,
    bot_token: str,
    backup_chat_id: str | int | None,
    layout: BackupLayout,
    *,
    logger: logging.Logger | None = None,
) -> tuple[int, int]:
    logger = logger or logging.getLogger(__name__)
    chat_id = _parse_chat_id(backup_chat_id)
    if chat_id is None:
        return 0, 0
    try:
        chat = await bot.get_chat(chat_id)
        pinned = _pinned_backup(chat)
        if pinned is None:
            logger.info("No hay una copia fijada de Telegram para restaurar.")
            return 0, 0
        snapshot = await _download_message(bot, pinned, bot_token)
        restored, restored_bytes = restore_snapshot(snapshot, layout)
        _write_message_pointer(layout, getattr(pinned, "message_id", 0))
        logger.info(
            "Respaldo restaurado de Telegram: auth_path=%s archivos=%d bytes=%d "
            "restaurados=%d bytes_restaurados=%d",
            layout.auth_dir,
            len(snapshot["files"]),
            _snapshot_total_bytes(snapshot),
            restored,
            restored_bytes,
        )
        return restored, restored_bytes
    except BackupError as exc:
        logger.error("No se pudo restaurar la copia de Telegram: %s", exc)
        raise
    except Exception as exc:
        # No registrar el mensaje de error de la API: puede incluir la URL autenticada del bot.
        logger.error("No se pudo consultar/descargar la copia Telegram (%s).", type(exc).__name__)
        raise BackupError("No se pudo consultar o descargar la copia de Telegram.") from exc


async def backup_to_telegram(
    bot: Any,
    bot_token: str,
    backup_chat_id: str | int | None,
    layout: BackupLayout,
    *,
    logger: logging.Logger | None = None,
    max_total_bytes: int = MAX_BACKUP_DOCUMENT_BYTES,
) -> bool:
    logger = logger or logging.getLogger(__name__)
    chat_id = _parse_chat_id(backup_chat_id)
    if chat_id is None:
        return False

    old_message = None
    old_message_id = None
    previous_snapshot = None
    fallback_pinned_candidate = False
    try:
        try:
            chat = await bot.get_chat(chat_id)
            old_message = _pinned_backup(chat)
            if old_message is None:
                # Algunas respuestas de Bot API no conservan file_name/caption.
                # El mensaje fijado con documento sigue siendo el candidato único;
                # la validación cifrada posterior decidirá si es nuestro respaldo.
                old_message = _pinned_document_candidate(chat)
                fallback_pinned_candidate = old_message is not None
            old_message_id = (
                getattr(old_message, "message_id", None)
                if old_message is not None
                else _read_message_pointer(layout)
            )
            if old_message is None:
                # Algunos chats/instancias de Bot API no incluyen pinned_message
                # en getChat. El puntero local todavía permite editar el mismo
                # mensaje y evita publicar una copia nueva en cada actualización.
                old_message = _message_with_id(old_message_id)
        except Exception as exc:
            # No crear otra copia si no se puede consultar el mensaje existente.
            # Un reintento posterior editará la copia única cuando Telegram responda.
            old_message = None
            old_message_id = _read_message_pointer(layout)
            if not old_message_id:
                logger.error("No se pudo localizar la copia anterior (%s); se cancela sin publicar otra.", type(exc).__name__)
                raise BackupError("No se pudo localizar la copia existente; no se creará otra.") from exc
            logger.warning("No se pudo leer el mensaje fijado (%s); se reintentará editar message_id=%s.", type(exc).__name__, old_message_id)
        if old_message is not None:
            try:
                previous_snapshot = await _download_message(bot, old_message, bot_token)
            except BackupError as exc:
                logger.warning("La copia fijada anterior no se pudo validar (%s); se conserva.", type(exc).__name__)
                if fallback_pinned_candidate:
                    raise BackupError("El mensaje fijado no es una copia válida; se conserva sin modificar.") from exc

        encrypted, snapshot, total_bytes, file_count = build_encrypted_backup(
            layout,
            bot_token,
            previous_snapshot=previous_snapshot,
            max_total_bytes=max_total_bytes,
        )
        logger.info(
            "Preparando copia Telegram: auth_path=%s archivos=%d bytes=%d",
            layout.auth_dir,
            file_count,
            total_bytes,
        )
        caption = build_backup_caption(snapshot, total_bytes, file_count)
        uploaded = None
        # Telegram permite editar el documento de un mensaje existente. Así el
        # respaldo sigue ocupando un único mensaje fijado y no genera spam.
        if old_message is not None:
            if not hasattr(bot, "edit_message_media"):
                edit_error = BackupError("Telegram no ofrece edición de documentos en esta conexión.")
            else:
                edit_error = None
                try:
                    from telegram import InputMediaDocument

                    uploaded = await bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=old_message.message_id,
                        media=InputMediaDocument(
                            media=io.BytesIO(encrypted),
                            filename=BACKUP_FILENAME,
                            caption=caption,
                        ),
                    )
                except Exception as exc:
                    edit_error = exc
            if edit_error is not None:
                # No crear otro documento: una edición fallida no debe llenar
                # el grupo de copias. La anterior sigue siendo recuperable y
                # el siguiente ciclo volverá a intentar editarla.
                logger.warning("No se actualizó la copia fijada (%s); se conserva la anterior sin crear otra.", type(edit_error).__name__)
                raise BackupError("No se pudo editar la copia existente; la anterior se conserva.") from edit_error
        else:
            uploaded = await bot.send_document(
                chat_id=chat_id,
                document=io.BytesIO(encrypted),
                filename=BACKUP_FILENAME,
                caption=caption,
            )
        # Verificación de ida y vuelta antes de fijar o reemplazar la copia anterior.
        checked = await _download_message(bot, uploaded, bot_token)
        if checked.get("format") != FORMAT_NAME or checked.get("version") != FORMAT_VERSION:
            raise BackupFormatError("La copia subida no pasó la validación de lectura.")
        uploaded_id = getattr(uploaded, "message_id", None)
        if not uploaded_id:
            raise BackupError("Telegram no devolvió el identificador del documento subido.")
        pin_error = None
        try:
            if hasattr(bot, "pin_chat_message"):
                await bot.pin_chat_message(
                    chat_id=chat_id,
                    message_id=uploaded_id,
                    disable_notification=True,
                )
            elif hasattr(uploaded, "pin"):
                await uploaded.pin(disable_notification=True)
            else:
                raise BackupError("La conexión de Telegram no ofrece la operación de fijar mensajes.")
        except Exception as exc:
            pin_error = exc
            logger.error(
                "Telegram no permitió fijar la copia message_id=%s (%s); "
                "se conservará una sola copia sin fijarla.",
                uploaded_id,
                type(exc).__name__,
            )
        old_id_to_remove = getattr(old_message, "message_id", None) if old_message is not None else old_message_id
        if old_id_to_remove and old_id_to_remove != uploaded_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=old_id_to_remove)
            except Exception as exc:
                logger.warning("La nueva copia está fijada; no se pudo borrar la anterior (%s).", type(exc).__name__)
        _write_message_pointer(layout, uploaded_id)
        logger.info(
            "Copia Telegram validada%s: message_id=%s auth_path=%s archivos=%d bytes=%d",
            " y fijada" if pin_error is None else " (sin fijar)",
            uploaded_id,
            layout.auth_dir,
            file_count,
            total_bytes,
        )
        return True
    except BackupError as exc:
        logger.error("Copia Telegram cancelada: %s", exc)
        raise
    except Exception as exc:
        logger.error("Falló la copia Telegram (%s); se conserva la copia fijada anterior.", type(exc).__name__)
        raise BackupError("No se pudo completar la copia de Telegram; la copia anterior se conserva.") from exc


async def periodic_backup(
    bot: Any,
    bot_token: str,
    backup_chat_id: str | int | None,
    layout: BackupLayout,
    *,
    interval_seconds: int = 6 * 60 * 60,
    lock: asyncio.Lock | None = None,
    logger: logging.Logger | None = None,
) -> None:
    logger = logger or logging.getLogger(__name__)
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            if lock is None:
                await backup_to_telegram(bot, bot_token, backup_chat_id, layout, logger=logger)
            else:
                async with lock:
                    await backup_to_telegram(bot, bot_token, backup_chat_id, layout, logger=logger)
        except Exception as exc:
            logger.warning("Respaldo periódico omitido (%s).", type(exc).__name__)
