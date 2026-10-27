from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)
PLATFORM_SHORTLINK_HOSTS = {"fb.watch", "vm.tiktok.com", "vt.tiktok.com"}
PLATFORM_HOST_HINTS = {
    "facebook.com", "fb.watch", "youtube.com", "youtu.be", "instagram.com",
    "tiktok.com", "twitter.com", "x.com", "reddit.com", "twitch.tv",
}
YOUTUBE_PUBLIC_CLIENTS = ("mweb", "android_vr", "android", "tv_embedded", "mediaconnect")
CNV_ORIGIN = "https://frame.y2meta-uk.com"
CNV_KEY_URL = "https://cnv.cx/v2/sanity/key"
CNV_CONVERTER_URL = "https://cnv.cx/v2/converter"


def is_youtube_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


def _youtube_video_id(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").rstrip(".").lower()
    if host == "youtu.be":
        candidate = parts.path.lstrip("/").split("/")[0]
        return candidate or None
    if parts.query:
        from urllib.parse import parse_qs

        value = parse_qs(parts.query).get("v")
        if value and value[0]:
            return value[0]
    segments = [item for item in parts.path.split("/") if item]
    for index, segment in enumerate(segments):
        if segment in ("shorts", "embed", "live") and index + 1 < len(segments):
            return segments[index + 1]
    return None


def _safe_media_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w\s()\[\].-]", "", value or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned[:160] or fallback)


def _cnv_youtube_download(
    url: str,
    folder: Path,
    *,
    max_bytes: int,
    max_duration_seconds: int,
    progress_hook=None,
    is_cancelled=None,
) -> Path:
    """Download a public YouTube video through the public cnv.cx converter.

    The same public conversion service is used by the working WhatsApp bot
    base (webmiku-md): it resolves the media on its own network, so it keeps
    working when YouTube throttles direct yt-dlp access from data-center
    IPs. No cookies, login or access-control bypass is involved.
    """
    import requests

    video_id = _youtube_video_id(url)
    if not video_id or len(video_id) != 11:
        raise ValueError("No se pudo identificar el ID del video de YouTube.")

    headers = {"User-Agent": "Mozilla/5.0", "Origin": CNV_ORIGIN, "Accept": "application/json"}
    key_response = requests.get(f"{CNV_KEY_URL}?id={video_id}", headers=headers, timeout=20)
    key_response.raise_for_status()
    key = key_response.json().get("key")
    if not key:
        raise ValueError("El servicio de descarga no emitió la clave temporal.")

    started_at = time.monotonic()
    body = {
        "link": url,
        "format": "mp4",
        "videoQuality": "720",
        "filenameStyle": "pretty",
    }
    converted = requests.post(
        CNV_CONVERTER_URL,
        data=body,
        headers={**headers, "key": key},
        timeout=60,
    )
    converted.raise_for_status()
    payload = converted.json()
    if payload.get("error"):
        raise ValueError(str(payload.get("errorMsg") or "El servicio de descarga rechazó el video."))
    direct = payload.get("url") or ""
    if not direct.startswith("http"):
        raise ValueError("El servicio de descarga no devolvió un archivo.")

    suggested_name = _safe_media_name(payload.get("filename") or "", video_id)
    if not suggested_name.lower().endswith(".mp4"):
        suggested_name = f"{suggested_name}.mp4"
    target = folder / suggested_name

    received = 0
    with requests.get(
        direct,
        headers={"User-Agent": "Mozilla/5.0", "Referer": f"{CNV_ORIGIN}/"},
        stream=True,
        timeout=(30, 120),
    ) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        if max_bytes > 0 and total > max_bytes:
            raise ValueError(f"El vídeo supera el límite de {max_bytes} bytes.")
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 16):
                if is_cancelled and is_cancelled():
                    raise ValueError("Descarga cancelada.")
                if max_duration_seconds > 0 and time.monotonic() - started_at > max_duration_seconds:
                    raise ValueError("Se agotó el tiempo máximo de descarga.")
                if not chunk:
                    continue
                received += len(chunk)
                if max_bytes > 0 and received > max_bytes:
                    raise ValueError(f"El vídeo supera el límite de {max_bytes} bytes.")
                handle.write(chunk)
                if progress_hook:
                    progress_hook(received, total)
    if received <= 0:
        target.unlink(missing_ok=True)
        raise ValueError("El servicio de descarga devolvió un archivo vacío.")
    return target


@lru_cache(maxsize=512)
def _matching_platform_extractor(url: str):
    """Return a yt-dlp extractor for a platform page, excluding its catch-all IE."""
    try:
        from yt_dlp.extractor import gen_extractor_classes
    except ImportError as exc:  # pragma: no cover - covered by deployment dependency
        raise RuntimeError("Falta yt-dlp; instala las dependencias del bot.") from exc

    for extractor_class in gen_extractor_classes():
        try:
            if extractor_class.ie_key() == "Generic":
                continue
            if extractor_class.suitable(url):
                return extractor_class.ie_key()
        except Exception:
            continue
    return None


def is_supported_platform_url(url: str) -> bool:
    """Whether yt-dlp has a site-specific extractor for this URL."""
    if _matching_platform_extractor(url) is not None:
        return True
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    return any(host == domain or host.endswith("." + domain) for domain in PLATFORM_HOST_HINTS)


def is_play_store_page(url: str) -> bool:
    """Return whether URL is an app listing, not a direct APK resource."""
    parts = urlsplit(url)
    host = (parts.hostname or "").rstrip(".").lower()
    return host == "play.google.com" and parts.path.rstrip("/").startswith("/store/apps/details")


def _check_public_dns_target(url: str) -> None:
    """Reject private/local targets before handing a platform URL to yt-dlp."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Solo se admiten enlaces HTTP/HTTPS válidos.")
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("No se admiten hosts locales.")
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        try:
            addresses = socket.getaddrinfo(
                host,
                parts.port or (443 if parts.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ValueError("No se pudo validar el host del enlace.") from exc
        resolved = {ipaddress.ip_address(item[4][0].split("%", 1)[0]) for item in addresses}
        if not resolved or any(not address.is_global for address in resolved):
            raise ValueError("El enlace resuelve a una dirección privada o no pública.")
    else:
        if not address.is_global:
            raise ValueError("No se admiten direcciones IP privadas.")


def download_platform_video(
    url: str,
    output_dir: str | Path,
    *,
    max_bytes: int,
    max_duration_seconds: int = 24 * 60 * 60,
    progress_hook=None,
    is_cancelled=None,
) -> tuple[Path, str, int]:
    """Download one public video through yt-dlp and return (path, name, size).

    The caller owns and must remove output_dir after uploading the returned file.
    Only publicly accessible content is supported. This function never loads
    cookies or tries to bypass login, private-post, DRM, or platform restrictions.
    """
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError
    except ImportError as exc:  # pragma: no cover - covered by deployment dependency
        raise RuntimeError("Falta yt-dlp; instala las dependencias del bot.") from exc

    if not is_supported_platform_url(url):
        raise ValueError("La plataforma de este enlace no está soportada por yt-dlp.")
    _check_public_dns_target(url)

    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    started_at = time.monotonic()

    # YouTube suele bloquear las IPs de data-center con las que trabaja
    # yt-dlp ("Sign in to confirm you're not a bot"), incluso con videos
    # públicos. El conversor público cnv.cx resuelve el video en su propia
    # red y sigue funcionando, así que se intenta primero y yt-dlp queda
    # como respaldo.
    if is_youtube_url(url):
        try:
            cnv_path = _cnv_youtube_download(
                url,
                folder,
                max_bytes=max_bytes,
                max_duration_seconds=max_duration_seconds,
                progress_hook=progress_hook,
                is_cancelled=is_cancelled,
            )
            size = cnv_path.stat().st_size
            if size > 0:
                return cnv_path, cnv_path.name, size
            cnv_path.unlink(missing_ok=True)
        except Exception as cnv_error:
            if is_cancelled and is_cancelled():
                raise RuntimeError("Descarga cancelada.") from cnv_error
            log.warning("Ruta cnv.cx falló para %s: %s", url, cnv_error)
            for stale in folder.iterdir():
                if stale.is_file():
                    stale.unlink(missing_ok=True)

    def check_progress(data):
        if max_duration_seconds > 0 and time.monotonic() - started_at > max_duration_seconds:
            raise DownloadError("Se agotó el tiempo máximo de descarga.")
        if is_cancelled and is_cancelled():
            raise DownloadError("Descarga cancelada.")
        downloaded = int(data.get("downloaded_bytes") or 0)
        if max_bytes > 0 and downloaded > max_bytes:
            raise DownloadError(f"El vídeo supera el límite de {max_bytes} bytes.")
        if progress_hook and data.get("status") == "downloading":
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            progress_hook(downloaded, total)

    base_options = {
        # Prefer MP4 where available, but retain the platform's best video if it
        # only offers another format. ffmpeg merges separate video/audio streams.
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": str(folder / "%(title).160B [%(id)s].%(ext)s"),
        "noplaylist": True,
        "max_filesize": max_bytes,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 2,
        "progress_hooks": [check_progress],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "cachedir": False,
        "writeinfojson": False,
        "writethumbnail": False,
        # YouTube actualmente requiere ejecutar el desafío JavaScript para
        # resolver firmas y formatos. Docker instala Node.js y yt-dlp-ejs.
        "js_runtimes": {"node": {}},
    }
    try:
        with YoutubeDL(base_options) as downloader:
            downloader.extract_info(url, download=True)
    except DownloadError as exc:
        if is_cancelled and is_cancelled():
            raise RuntimeError("Descarga cancelada.") from exc
        message = str(exc).strip()
        lowered = message.lower()
        restricted_markers = (
            "sign in", "not a bot", "login required", "private video",
            "members-only", "members only",
        )
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
        is_youtube_host = host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")
        # YouTube may block the default web client on a data-center IP even
        # for public videos. Try public clients that do not require cookies or
        # access-control bypasses before reporting the block to the user.
        should_retry_youtube = is_youtube_host and (
            "403" in lowered or any(marker in lowered for marker in restricted_markers)
        )
        if should_retry_youtube:
            for client in YOUTUBE_PUBLIC_CLIENTS:
                retry_options = dict(base_options)
                retry_options["extractor_args"] = {"youtube": {"player_client": [client]}}
                try:
                    with YoutubeDL(retry_options) as downloader:
                        downloader.extract_info(url, download=True)
                except DownloadError as retry_exc:
                    message = str(retry_exc).strip()
                    lowered = message.lower()
                    continue
                else:
                    message = None
                    break
        if message is not None:
            if any(marker in lowered for marker in restricted_markers):
                raise ValueError(
                    "YouTube bloqueó esta descarga desde el servidor. "
                    "El video puede ser público, pero requiere otra red o cookies "
                    "de una sesión autorizada; el bot no usa cookies ni intenta eludir "
                    "controles de acceso."
                ) from exc
            raise ValueError(message[:300] or "No se pudo descargar el enlace público.") from exc

    candidates = [
        path for path in folder.iterdir()
        if path.is_file() and not path.name.endswith((".part", ".ytdl"))
    ]
    if not candidates:
        raise ValueError("La plataforma no produjo ningún archivo de vídeo descargable.")
    if len(candidates) > 1:
        # No playlists are requested; choose the largest completed media object
        # if a site happens to leave a sidecar behind despite disabled metadata.
        candidates.sort(key=lambda path: path.stat().st_size, reverse=True)
    video_path = candidates[0]
    size = video_path.stat().st_size
    if size <= 0:
        raise ValueError("La plataforma devolvió un archivo vacío.")
    if max_bytes > 0 and size > max_bytes:
        raise ValueError(f"El vídeo supera el límite de {max_bytes} bytes.")
    return video_path, video_path.name, size
