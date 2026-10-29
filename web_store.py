"""Servidor estático de la tienda ToDus S3 para el dominio generado por Railway."""
from __future__ import annotations

import mimetypes
import json
import os
import re
import time
from html import escape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Callable
from urllib.parse import parse_qs, unquote, urlsplit
import sync_api
import todus_s3_storage
from sync_api import consume_pairing_code, deliveries_for_token, revoke_token
from todus_s3_storage import delete_from_todus_s3

WEB_ROOT = Path(__file__).resolve().parent / "web"
SITE_STARTED_AT = time.time()
_VISITOR_LOCK = Lock()
_VISITOR_LAST_SEEN: dict[str, float] = {}
_TOTAL_VISITS = 0
_MAX_VISITORS = 5000
_VISITOR_RE = re.compile(r"^[A-Za-z0-9-]{8,96}$")
_DEFAULT_ORIGIN = "https://tod4s3-production.up.railway.app"
_PROFILE_CHECKER: Callable[[int], dict] | None = None


def _validated_delivery_url(url: str, user_id: int) -> bool:
    """Check that a saved part belongs to this user's configured ToDus namespace."""
    if not isinstance(url, str) or not url:
        return False
    try:
        parsed = urlsplit(url)
        base = urlsplit(todus_s3_storage.TODUS_S3_BASE_URL)
        if (parsed.scheme != "https" or base.scheme != "https"
                or not parsed.hostname or not base.hostname
                or parsed.hostname.lower() != base.hostname.lower()
                or parsed.port != base.port
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            return False
    except ValueError:
        return False
    base_path = base.path.rstrip("/")
    expected_prefix = f"{base_path}/users/tg_{int(user_id)}/telegram/"
    if not parsed.path.startswith(expected_prefix):
        return False
    remainder = parsed.path[len(expected_prefix):]
    if not remainder:
        return False
    # Prevent encoded separators and traversal segments from changing the key
    # interpreted by the storage service after URL decoding.
    for segment in remainder.split("/"):
        decoded = unquote(segment)
        if decoded in {"", ".", ".."} or "/" in decoded or "\\" in decoded:
            return False
    return True


def delete_delivery_for_token(token: str, task_id: str) -> tuple[dict, int]:
    """Delete an owned delivery; network calls occur outside sync_api's lock."""
    user_id = sync_api.user_id_for_token(token)
    if user_id is None:
        return {"ok": False, "error": "unauthorized"}, 401
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
        return {"ok": False, "error": "invalid_task_id"}, 400
    task_id = task_id.strip()
    row = sync_api.delivery_for_user(user_id, task_id)
    if row is None:
        if sync_api.has_delivery_without_task_id(user_id):
            return {"ok": False, "error": "legacy_delivery_incomplete",
                    "message": "Hay una entrega antigua sin task_id; no se intentó borrar ninguna parte."}, 409
        return {"ok": False, "error": "delivery_not_found"}, 404
    if row.get("source", "tod4s3_bot") != "tod4s3_bot":
        return {"ok": False, "error": "delivery_source_not_supported",
                "message": "Esta entrega no fue creada por el bot y no se puede borrar desde aquí."}, 409
    parts = row.get("parts")
    if row.get("task_id") != task_id or not isinstance(parts, list) or not parts:
        return {"ok": False, "error": "legacy_delivery_incomplete",
                "message": "La entrega no contiene un task_id o partes válidas; no se intentó borrar."}, 409
    if any(not _validated_delivery_url(url, user_id) for url in parts):
        return {"ok": False, "error": "invalid_delivery_url",
                "message": "La entrega contiene una URL que no pertenece al espacio ToDus de este usuario."}, 409
    try:
        for url in parts:
            delete_from_todus_s3(url)
    except Exception:
        # Do not expose storage responses, URLs, credentials, or exception text.
        return {"ok": False, "error": "delete_failed",
                "message": "No se pudieron borrar todas las partes; la entrega se conserva para reintentar."}, 502
    if not sync_api.remove_delivery_for_user(user_id, task_id, row):
        return {"ok": False, "error": "delivery_changed",
                "message": "La entrega cambió durante el borrado; la fila se conserva."}, 409
    return {"ok": True, "task_id": task_id, "deleted_parts": len(parts)}, 200


def _safe_site_origin(handler: "StoreHandler") -> str:
    """Obtiene un origen seguro sin reflejar un Host arbitrario en HTML."""
    configured = os.getenv("PUBLIC_SITE_URL", "").strip().rstrip("/")
    if configured:
        configured = configured.split("#", 1)[0].rstrip("/")
        if re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d+)?", configured):
            return configured
    forwarded_proto = handler.headers.get("X-Forwarded-Proto", "https").split(",", 1)[0].strip().lower()
    proto = forwarded_proto if forwarded_proto in {"http", "https"} else "https"
    host = handler.headers.get("Host", "").split(",", 1)[0].strip()
    if re.fullmatch(r"[A-Za-z0-9.-]+(?::\d{1,5})?", host):
        return escape(f"{proto}://{host}", quote=True)
    return _DEFAULT_ORIGIN


class StoreHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_GET(self):  # noqa: N802
        parsed = urlsplit(unquote(self.path))
        path = parsed.path
        if path == "/api/app/profile":
            authorization = self.headers.get("Authorization", "")
            token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
            user_id = sync_api.user_id_for_token(token)
            if user_id is None:
                self._json_response({"ok": False, "error": "unauthorized"}, 401)
                return
            checker = _PROFILE_CHECKER
            if checker is None:
                self._json_response({"ok": False, "error": "profile_unavailable"}, 503)
                return
            try:
                profile = checker(user_id)
                if (not isinstance(profile, dict)
                        or not isinstance(profile.get("premium"), bool)
                        or not isinstance(profile.get("plan"), str)
                        or not profile["plan"]):
                    raise ValueError("invalid_profile")
            except Exception:
                self._json_response({"ok": False, "error": "profile_unavailable"}, 503)
                return
            self._json_response({"premium": profile["premium"], "plan": profile["plan"]})
            return
        if path == "/api/app/deliveries":
            token = self.headers.get("Authorization", "")
            token = token[7:].strip() if token.lower().startswith("bearer ") else ""
            deliveries = deliveries_for_token(token)
            if deliveries is None:
                self._json_response({"ok": False, "error": "unauthorized"}, 401)
            else:
                self._json_response({"ok": True, "deliveries": deliveries})
            return
        if path == "/api/site-stats":
            visitor = parse_qs(parsed.query).get("visitor", [""])[0][:96]
            if not _VISITOR_RE.fullmatch(visitor):
                visitor = ""
            now = time.time()
            global _TOTAL_VISITS, _VISITOR_LAST_SEEN
            with _VISITOR_LOCK:
                if visitor and visitor not in _VISITOR_LAST_SEEN:
                    _TOTAL_VISITS += 1
                if visitor:
                    _VISITOR_LAST_SEEN[visitor] = now
                cutoff = now - 90
                active = sum(1 for seen in _VISITOR_LAST_SEEN.values() if seen >= cutoff)
                # Keep the in-memory table bounded on a long-running service.
                _VISITOR_LAST_SEEN = {key: seen for key, seen in _VISITOR_LAST_SEEN.items() if seen >= cutoff}
                if len(_VISITOR_LAST_SEEN) > _MAX_VISITORS:
                    oldest = sorted(_VISITOR_LAST_SEEN, key=_VISITOR_LAST_SEEN.get)
                    for key in oldest[:len(_VISITOR_LAST_SEEN) - _MAX_VISITORS]:
                        _VISITOR_LAST_SEEN.pop(key, None)
                payload = {
                    "total_visits": _TOTAL_VISITS,
                    "online_now": active,
                    "uptime_seconds": max(0, int(now - SITE_STARTED_AT)),
                }
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in {"/health", "/healthz"}:
            body = b'{"ok":true,"service":"toduss3store"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/manus-routes.json":
            self.path = "/manus-routes.json"
        elif path == "/":
            origin = _safe_site_origin(self)
            body = (WEB_ROOT / "index.html").read_bytes().replace(b"__SITE_ORIGIN__", origin.encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802
        parsed = urlsplit(unquote(self.path))
        if parsed.path not in {"/api/app/pair", "/api/app/revoke", "/api/app/deliveries/delete"}:
            self._json_response({"ok": False, "error": "not_found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 8192:
                raise ValueError("body_too_large")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("invalid_body")
        except (ValueError, TypeError, json.JSONDecodeError):
            self._json_response({"ok": False, "error": "invalid_body"}, 400)
            return
        if parsed.path == "/api/app/pair":
            token = consume_pairing_code(body.get("code", ""), body.get("device_id", ""))
            if not token:
                self._json_response({"ok": False, "error": "invalid_or_expired_code"}, 401)
            else:
                self._json_response({"ok": True, "token": token})
            return
        token = self.headers.get("Authorization", "")
        token = token[7:].strip() if token.lower().startswith("bearer ") else ""
        if parsed.path == "/api/app/deliveries/delete":
            payload, status = delete_delivery_for_token(token, body.get("task_id"))
            self._json_response(payload, status)
            return
        if not revoke_token(token):
            self._json_response({"ok": False, "error": "unauthorized"}, 401)
        else:
            self._json_response({"ok": True})

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _json_response(self, payload: dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def log_message(self, format, *args):
        # Mantener los logs de Railway útiles sin registrar parámetros privados.
        print(f"[web] {self.address_string()} - {format % args}", flush=True)

    def end_headers(self):  # noqa: N802
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; media-src 'self'; "
            "connect-src 'self'; base-uri 'self'; form-action 'self'",
        )
        super().end_headers()


def start_store_server(profile_checker: Callable[[int], dict] | None = None) -> Thread:
    global _PROFILE_CHECKER
    _PROFILE_CHECKER = profile_checker
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), StoreHandler)
    thread = Thread(target=server.serve_forever, name="todus-store-http", daemon=True)
    thread.start()
    print(f"Tienda ToDus S3 disponible en el puerto {port}", flush=True)
    return thread
