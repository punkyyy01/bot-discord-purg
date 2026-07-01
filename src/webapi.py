"""Web API pública: galería de GIFs de PURG4TORY + health check."""

import logging
import time

from aiohttp import web

from config import PURGATORY_GUILD_ID, WEB_PORT
from db import count_gif_urls, delete_gif_url_by_id, list_gif_urls, save_gif_url
from gif_gallery import GIF_GALLERY_HTML
from utils import LRUDict
import r2

log = logging.getLogger(__name__)

_rate_post: LRUDict = LRUDict(512)
_rate_delete: LRUDict = LRUDict(512)
_runner: web.AppRunner | None = None


def _rate_ok(store: LRUDict, ip: str, limit: int, window: float = 60.0) -> bool:
    now = time.monotonic()
    ts = [t for t in store.get(ip, []) if now - t < window]
    if len(ts) >= limit:
        store[ip] = ts
        return False
    ts.append(now)
    store[ip] = ts
    return True


def _valid_gif_url(url: str) -> bool:
    if "tenor.com" in url or "giphy.com" in url:
        return True
    pub = r2.public_url()
    return bool(pub and url.startswith(pub))


@web.middleware
async def _cors_middleware(request: web.Request, handler) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        })
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


async def _api_gif_list(request: web.Request) -> web.Response:
    gifs = await list_gif_urls(PURGATORY_GUILD_ID)
    return web.json_response({"gifs": gifs, "total": len(gifs)})


async def _api_gif_add(request: web.Request) -> web.Response:
    ip = request.remote or "unknown"
    if not _rate_ok(_rate_post, ip, 5):
        return web.json_response({"error": "rate limit"}, status=429)
    try:
        data = await request.json()
        url = (data.get("url") or "").strip()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    if not url or not _valid_gif_url(url):
        return web.json_response({"error": "url inválida o no permitida"}, status=400)
    inserted = await save_gif_url(PURGATORY_GUILD_ID, url)
    total = await count_gif_urls(PURGATORY_GUILD_ID)
    return web.json_response({"inserted": inserted, "total": total})


async def _api_gif_delete(request: web.Request) -> web.Response:
    ip = request.remote or "unknown"
    if not _rate_ok(_rate_delete, ip, 3):
        return web.json_response({"error": "rate limit"}, status=429)
    try:
        gif_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "id inválido"}, status=400)
    deleted = await delete_gif_url_by_id(PURGATORY_GUILD_ID, gif_id)
    return web.json_response({"deleted": deleted})


async def _api_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _gallery(request: web.Request) -> web.Response:
    return web.Response(text=GIF_GALLERY_HTML, content_type="text/html", charset="utf-8")


async def start_web_server() -> None:
    global _runner
    if _runner is not None:
        return
    app = web.Application(middlewares=[_cors_middleware])
    app.router.add_get("/", _gallery)
    app.router.add_get("/api/gifs", _api_gif_list)
    app.router.add_post("/api/gifs", _api_gif_add)
    app.router.add_delete("/api/gifs/{id}", _api_gif_delete)
    app.router.add_get("/health", _api_health)
    _runner = web.AppRunner(app)
    await _runner.setup()
    site = web.TCPSite(_runner, "0.0.0.0", WEB_PORT)
    await site.start()
    log.info("Web API iniciada en 0.0.0.0:%s", WEB_PORT)


async def stop_web_server() -> None:
    global _runner
    if _runner is not None:
        await _runner.cleanup()
        _runner = None
