from __future__ import annotations

import mimetypes
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TODUS_S3_BASE_URL = "https://s3.todus.cu/stream"
CHUNK_SIZE = 1 << 20
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2
mimetypes.add_type("application/vnd.android.package-archive", ".apk")


class ToDusS3UploadError(RuntimeError):
    """La subida a ToDus S3 no pudo completarse."""


class ToDusS3UploadCancelled(ToDusS3UploadError):
    """La subida se canceló antes de completarse."""


def delete_from_todus_s3(url: str, *, timeout_seconds: int = 60, base_url: str = TODUS_S3_BASE_URL) -> None:
    """Elimina y verifica un objeto previamente subido por este bot."""
    if not url.startswith(base_url.rstrip("/") + "/"):
        raise ValueError("La URL no pertenece al almacenamiento ToDus configurado.")
    timeout = max(10, int(timeout_seconds))
    headers = {
        "User-Agent": "JK-Telegram-Bot/1.0",
        "Cache-Control": "no-cache, no-store",
        "Pragma": "no-cache",
    }
    try:
        response = requests.delete(url, timeout=timeout, headers=headers)
    except requests.RequestException as exc:
        raise ToDusS3UploadError(f"No se pudo conectar con ToDus S3 ({type(exc).__name__}).") from exc
    if response.status_code not in {200, 204, 404}:
        raise ToDusS3UploadError(f"ToDus S3 respondió HTTP {response.status_code} al borrar.")

    # Un DELETE aceptado no garantiza que una CDN o caché ya haya dejado de
    # servir el objeto. Consultamos una URL con query aleatoria para saltar la
    # caché y no damos por terminado el borrado mientras siga respondiendo.
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["hard_delete_check"] = uuid.uuid4().hex
    verify_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    last_status = None
    for attempt in range(3):
        try:
            check = requests.head(verify_url, timeout=min(timeout, 30), headers=headers, allow_redirects=True)
            last_status = check.status_code
            if check.status_code in {404, 410}:
                return
            if check.status_code == 405:
                check = requests.get(
                    verify_url,
                    timeout=min(timeout, 30),
                    headers={**headers, "Range": "bytes=0-0"},
                    stream=True,
                    allow_redirects=True,
                )
                last_status = check.status_code
                check.close()
                if last_status in {404, 410}:
                    return
            if last_status not in {404, 410}:
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise ToDusS3UploadError(
                    f"ToDus todavía sirve el objeto después de borrarlo (HTTP {last_status})."
                )
        except requests.RequestException as exc:
            if attempt == 2:
                raise ToDusS3UploadError(
                    f"No se pudo verificar el borrado en ToDus ({type(exc).__name__})."
                ) from exc
            time.sleep(1 + attempt)
    raise ToDusS3UploadError(f"No se pudo verificar el borrado en ToDus (HTTP {last_status}).")


def build_object_key(
    telegram_user_id: int,
    now: datetime | None = None,
    filename: str | None = None,
    object_prefix: str | None = None,
) -> str:
    if isinstance(telegram_user_id, bool) or int(telegram_user_id) <= 0:
        raise ValueError("El ID de Telegram debe ser un entero positivo.")

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    folder_id = uuid.uuid4().hex[:6]
    file_id = uuid.uuid4().hex[:12]
    original_name = Path(filename or "download.bin").name
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name).strip("._") or "download.bin"
    extension = Path(safe_name).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,16}", extension):
        extension = ".bin"
        safe_name = f"{Path(safe_name).stem or 'download'}{extension}"
    if object_prefix is None:
        prefix = f"users/tg_{int(telegram_user_id)}/telegram"
    else:
        prefix_parts = str(object_prefix).strip("/").split("/")
        if not prefix_parts or any(
            part in {"", ".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", part)
            for part in prefix_parts
        ):
            raise ValueError("El prefijo de almacenamiento no es válido.")
        prefix = "/".join(prefix_parts)
    return f"{prefix}/{stamp}-{folder_id}/telegram_{file_id}_{safe_name}"


def _content_disposition(filename: str) -> str:
    name = Path(filename).name or "download.bin"
    ascii_fallback = "".join(
        char if 32 <= ord(char) < 127 and char not in {'"', "\\", ";"} else "_"
        for char in name
    ).strip() or "download.bin"
    encoded = quote(name, safe="")
    disposition = "attachment" if Path(name).suffix.lower() == ".apk" else "inline"
    return f'{disposition}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


class _ProgressStream:
    """File-like iterable that streams bytes and reports progress to the bot."""

    def __init__(
        self,
        file_obj,
        total: int,
        *,
        on_progress=None,
        is_cancelled=None,
        deadline: float,
    ) -> None:
        self._file = file_obj
        self._total = total
        self._sent = 0
        self._on_progress = on_progress
        self._is_cancelled = is_cancelled
        self._deadline = deadline

    def __len__(self) -> int:
        return max(0, self._total - self._sent)

    def _check(self) -> None:
        if self._is_cancelled and self._is_cancelled():
            raise ToDusS3UploadCancelled("Subida cancelada.")
        if time.monotonic() > self._deadline:
            raise ToDusS3UploadError("Se agotó el tiempo máximo de subida a ToDus S3.")

    def read(self, size: int = -1) -> bytes:
        self._check()
        chunk = self._file.read(CHUNK_SIZE if size is None or size < 0 else size)
        if chunk:
            self._sent += len(chunk)
            if self._on_progress:
                try:
                    self._on_progress(self._sent, self._total)
                except Exception:
                    # Un fallo al editar el mensaje de progreso no debe cancelar la subida.
                    pass
        return chunk

    def __iter__(self):
        while True:
            chunk = self.read(CHUNK_SIZE)
            if not chunk:
                break
            yield chunk

    def __getattr__(self, name):
        # requests usa tell/fileno para determinar Content-Length cuando es posible.
        return getattr(self._file, name)


def upload_to_todus_s3(
    local_path: str | Path,
    filename: str,
    telegram_user_id: int,
    *,
    on_progress=None,
    timeout_seconds: int = 24 * 60 * 60,
    is_cancelled=None,
    base_url: str = TODUS_S3_BASE_URL,
    object_prefix: str | None = None,
) -> tuple[str, int]:
    """Upload a file with an unauthenticated PUT to ToDus S3 and return its public URL.

    By default, the Telegram user ID forms part of the object namespace; callers
    may provide a validated object_prefix to use a configured destination folder.
    No ToDus account, phone number, password or Authorization header is required.
    """
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"Archivo no encontrado: {path}")
    size = path.stat().st_size
    key = build_object_key(telegram_user_id, filename=filename, object_prefix=object_prefix)
    url = f"{base_url.rstrip('/')}/{quote(key, safe='/')}"
    content_type = mimetypes.guess_type(Path(filename).name)[0] or "application/octet-stream"
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(size),
        "Content-Disposition": _content_disposition(filename),
        "User-Agent": "JK-Telegram-Bot/1.0",
    }
    timeout = max(30, int(timeout_seconds))
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    with requests.Session() as session:
        for attempt in range(MAX_RETRIES):
            if is_cancelled and is_cancelled():
                raise ToDusS3UploadCancelled("Subida cancelada.")
            try:
                with path.open("rb") as file_obj:
                    body = _ProgressStream(
                        file_obj,
                        size,
                        on_progress=on_progress,
                        is_cancelled=is_cancelled,
                        deadline=deadline,
                    )
                    response = session.put(
                        url,
                        data=body,
                        headers=headers,
                        timeout=(30, timeout),
                    )
            except ToDusS3UploadCancelled:
                raise
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < MAX_RETRIES and time.monotonic() < deadline:
                    time.sleep(min(RETRY_BACKOFF_SECONDS**attempt, 8))
                    continue
                raise ToDusS3UploadError(
                    f"No se pudo conectar con ToDus S3 ({type(exc).__name__})."
                ) from exc

            if response.status_code == 200:
                return url, size
            if response.status_code >= 500 and attempt + 1 < MAX_RETRIES:
                time.sleep(min(RETRY_BACKOFF_SECONDS**attempt, 8))
                continue
            raise ToDusS3UploadError(
                f"ToDus S3 respondió HTTP {response.status_code}."
            )

    if last_error:
        raise ToDusS3UploadError(
            f"No se pudo conectar con ToDus S3 ({type(last_error).__name__})."
        ) from last_error
    raise ToDusS3UploadError("No se pudo completar la subida a ToDus S3.")
