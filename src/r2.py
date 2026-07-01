"""Cliente Cloudflare R2 con inicialización perezosa.

El cliente se crea la primera vez que se necesita; si faltan variables de
entorno el módulo igual importa sin romper nada y available() devuelve False.
"""

import asyncio
import hashlib
import logging
import os

import requests

log = logging.getLogger(__name__)

_client = None
_checked = False

# Sentinel: el GIF supera el límite de tamaño (no guardar en DB, no reintentar).
GIF_TOO_LARGE = ""

_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def public_url() -> str:
    return os.getenv("R2_PUBLIC_URL", "").strip()


def _bucket() -> str:
    return os.getenv("R2_BUCKET_NAME", "").strip()


def get_client():
    global _client, _checked
    if not _checked:
        _checked = True
        endpoint = os.getenv("R2_ENDPOINT_URL", "").strip()
        key_id = os.getenv("R2_ACCESS_KEY_ID", "").strip()
        secret = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
        if endpoint and key_id and secret and _bucket():
            import boto3
            from botocore.config import Config

            _client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=key_id,
                aws_secret_access_key=secret,
                config=Config(signature_version="s3v4"),
                region_name="auto",
            )
    return _client


def available() -> bool:
    return get_client() is not None


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.getenv(name, "") or default)
        return v if v > 0 else default
    except (ValueError, TypeError):
        return default


def upload_gif_sync(url: str, guild_id: int) -> str | None:
    """Retorna URL de R2, '' (GIF_TOO_LARGE) si supera el límite, None en otros errores."""
    client = get_client()
    if client is None:
        return None
    max_bytes = _env_int("MAX_GIF_DOWNLOAD_BYTES", 8 * 1024 * 1024)
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; bot)"}
        resp = requests.get(url, headers=headers, timeout=15, stream=True)
        if resp.status_code != 200:
            log.error("HTTP %s al descargar GIF para R2: %s", resp.status_code, url)
            return None
        cl = resp.headers.get("Content-Length")
        if cl and int(cl) > max_bytes:
            log.debug("GIF descartado (Content-Length %s > %d): %s", cl, max_bytes, url)
            resp.close()
            return GIF_TOO_LARGE
        data = resp.content
        resp.close()
        if len(data) > max_bytes:
            log.debug("GIF descartado (%d bytes > %d): %s", len(data), max_bytes, url)
            return GIF_TOO_LARGE
        key = f"{guild_id}/{hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()}.gif"
        client.put_object(
            Bucket=_bucket(),
            Key=key,
            Body=data,
            ContentType="image/gif",
        )
        return f"{public_url().rstrip('/')}/{key}"
    except Exception:
        log.exception("Error subiendo GIF a R2: %s", url)
        return None


def upload_image_sync(url: str, guild_id: int, ext: str) -> str | None:
    client = get_client()
    if client is None:
        return None
    content_type = _IMAGE_CONTENT_TYPES.get(ext.lower(), "image/png")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; bot)"}
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            log.error("HTTP %s al descargar imagen para R2: %s", resp.status_code, url)
            return None
        data = resp.content
        key = f"{guild_id}/{hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()}{ext}"
        client.put_object(
            Bucket=_bucket(),
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return f"{public_url().rstrip('/')}/{key}"
    except Exception:
        log.exception("Error subiendo imagen a R2: %s", url)
        return None


async def delete_url(url: str) -> None:
    """Borra un objeto de R2 si la URL le pertenece. No-op para URLs externas."""
    pub = public_url()
    if not pub or not url.startswith(pub):
        return
    client = get_client()
    if client is None:
        return
    key = url[len(pub.rstrip("/")) + 1:]
    try:
        await asyncio.to_thread(client.delete_object, Bucket=_bucket(), Key=key)
    except Exception:
        log.warning("No se pudo eliminar objeto de R2: %s", url)
