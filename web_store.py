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
from urllib.parse import parse_qs, unquote, urlsplit
from sync_api import consume_pairing_code, deliveries_for_token, revoke_token

WEB_ROOT = Path(__file__).resolve().parent / "web"
SITE_STARTED_AT = time.time()
_VISITOR_LOCK = Lock()
_VISITOR_LAST_SEEN: dict[str, float] = {}
_TOTAL_VISITS = 0
_MAX_VISITORS = 5000
_VISITOR_RE = re.compile(r"^[A-Za-z0-9-]{8,96}$")
_DEFAULT_ORIGIN = "https://tod4s3-production.up.railway.app"


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
        if parsed.path not in {"/api/app/pair", "/api/app/revoke"}:
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


def start_store_server() -> Thread:
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), StoreHandler)
    thread = Thread(target=server.serve_forever, name="todus-store-http", daemon=True)
    thread.start()
    print(f"Tienda ToDus S3 disponible en el puerto {port}", flush=True)
    return thread
