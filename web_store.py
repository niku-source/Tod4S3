"""Servidor estático de la tienda ToDus S3 para el dominio generado por Railway."""
from __future__ import annotations

import mimetypes
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import unquote, urlsplit

WEB_ROOT = Path(__file__).resolve().parent / "web"


class StoreHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_GET(self):  # noqa: N802
        path = urlsplit(unquote(self.path)).path
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
            origin = f"{self.headers.get('X-Forwarded-Proto', 'https').split(',')[0].strip()}://{self.headers.get('Host', '')}"
            body = (WEB_ROOT / "index.html").read_bytes().replace(b"__SITE_ORIGIN__", origin.encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def log_message(self, format, *args):
        # Mantener los logs de Railway útiles sin registrar parámetros privados.
        print(f"[web] {self.address_string()} - {format % args}", flush=True)


def start_store_server() -> Thread:
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), StoreHandler)
    thread = Thread(target=server.serve_forever, name="todus-store-http", daemon=True)
    thread.start()
    print(f"Tienda ToDus S3 disponible en el puerto {port}", flush=True)
    return thread
