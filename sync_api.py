"""Estado compartido y autenticación mínima para el cliente Tod4S3 Sync."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("BOT_DATA_DIR", "/data"))
STATE_FILE = Path(os.getenv("SYNC_APP_STATE_FILE", str(DATA_DIR / "sync_app.json")))
_LOCK = threading.RLock()
_PAIRING_TTL = 10 * 60
_TOKEN_TTL = 365 * 24 * 60 * 60


def _default_state() -> dict[str, Any]:
    return {"pairings": {}, "tokens": {}, "deliveries": {}}


def _read() -> dict[str, Any]:
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            state = _default_state()
            state.update({k: v for k, v in raw.items() if k in state})
            return state
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return _default_state()


def _write(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="sync_app_", suffix=".json", dir=str(STATE_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, STATE_FILE)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def create_pairing_code(user_id: int) -> str:
    """Crea un código de un solo uso que caduca en diez minutos."""
    with _LOCK:
        state = _read()
        now = int(time.time())
        pairings = {
            code: item for code, item in state["pairings"].items()
            if isinstance(item, dict) and int(item.get("expires_at", 0)) > now
        }
        code = secrets.token_hex(4).upper()
        pairings[code] = {"user_id": int(user_id), "expires_at": now + _PAIRING_TTL}
        state["pairings"] = pairings
        _write(state)
        return code


def consume_pairing_code(code: str, device_id: str) -> str | None:
    """Intercambia el código por un token persistente del dispositivo."""
    code = str(code or "").strip().upper()
    device_id = str(device_id or "").strip()[:128]
    if not code or not device_id:
        return None
    with _LOCK:
        state = _read()
        item = state["pairings"].get(code)
        if not isinstance(item, dict) or int(item.get("expires_at", 0)) <= int(time.time()):
            state["pairings"].pop(code, None)
            _write(state)
            return None
        token = "t4s3_" + secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        state["tokens"][token_hash] = {
            "user_id": int(item["user_id"]),
            "device_id": device_id,
            "created_at": int(time.time()),
        }
        state["pairings"].pop(code, None)
        _write(state)
        return token


def revoke_token(token: str) -> bool:
    token_hash = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
    with _LOCK:
        state = _read()
        removed = state["tokens"].pop(token_hash, None) is not None
        if removed:
            _write(state)
        return removed


def user_id_for_token(token: str) -> int | None:
    token_hash = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
    with _LOCK:
        state = _read()
        item = state["tokens"].get(token_hash)
        if not isinstance(item, dict):
            return None
        if int(item.get("created_at", 0)) + _TOKEN_TTL <= int(time.time()):
            state["tokens"].pop(token_hash, None)
            _write(state)
            return None
        return int(item["user_id"])


def record_delivery(user_id: int, task_id: str, filename: str, size: int,
                    parts: list[str]) -> None:
    """Publica una entrega reciente para el usuario vinculado."""
    if not parts:
        return
    with _LOCK:
        state = _read()
        key = str(int(user_id))
        deliveries = state["deliveries"].get(key, [])
        if not isinstance(deliveries, list):
            deliveries = []
        deliveries.insert(0, {
            "task_id": str(task_id),
            "source": "tod4s3_bot",
            "filename": str(filename or "archivo")[:180],
            "size": max(0, int(size or 0)),
            "parts": [str(url) for url in parts if str(url).startswith("https://")],
            "created_at": int(time.time()),
        })
        state["deliveries"][key] = deliveries[:30]
        _write(state)


def deliveries_for_token(token: str) -> list[dict[str, Any]] | None:
    user_id = user_id_for_token(token)
    if user_id is None:
        return None
    with _LOCK:
        state = _read()
        rows = state["deliveries"].get(str(user_id), [])
        if not isinstance(rows, list):
            return []
        return [
            ({**row, "source": "tod4s3_bot"} if "source" not in row else row)
            for row in rows
            if isinstance(row, dict) and row.get("source", "tod4s3_bot") == "tod4s3_bot"
        ]


def delivery_for_user(user_id: int, task_id: str) -> dict[str, Any] | None:
    """Devuelve una copia de una entrega cuyo propietario ya se verificó."""
    with _LOCK:
        state = _read()
        rows = state["deliveries"].get(str(int(user_id)), [])
        if not isinstance(rows, list):
            return None
        for row in rows:
            if isinstance(row, dict) and row.get("task_id") == task_id:
                return dict(row)
    return None


def has_delivery_without_task_id(user_id: int) -> bool:
    """Indica si hay entregas legacy que no pueden identificarse para borrado."""
    with _LOCK:
        state = _read()
        rows = state["deliveries"].get(str(int(user_id)), [])
        return isinstance(rows, list) and any(
            isinstance(row, dict)
            and row.get("source", "tod4s3_bot") == "tod4s3_bot"
            and (not isinstance(row.get("task_id"), str) or not row.get("task_id").strip())
            for row in rows
        )


def remove_delivery_for_user(user_id: int, task_id: str,
                             expected_row: dict[str, Any]) -> bool:
    """Elimina localmente la fila solo si no cambió durante el borrado remoto."""
    with _LOCK:
        state = _read()
        key = str(int(user_id))
        rows = state["deliveries"].get(key, [])
        if not isinstance(rows, list):
            return False
        for index, row in enumerate(rows):
            if isinstance(row, dict) and row.get("task_id") == task_id:
                if row != expected_row:
                    return False
                del rows[index]
                state["deliveries"][key] = rows
                _write(state)
                return True
    return False
