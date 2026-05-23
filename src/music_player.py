import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import discord
import yt_dlp

log = logging.getLogger(__name__)

EMBED_COLOR = 0x8B00FF

COOKIES_FILE = os.getenv("YTDLP_COOKIES", "/opt/bot-discord-purg/cookies.txt")

_YT_RE = re.compile(
    r'^https?://(?:www\.|m\.|music\.)?(?:youtube\.com/|youtu\.be/)',
    re.IGNORECASE,
)
_URL_RE = re.compile(r'^https?://', re.IGNORECASE)


class YouTubeNotAllowed(Exception):
    pass


class MediaFetchError(Exception):
    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


def _is_youtube_info(info: dict) -> bool:
    extractor = (info.get('extractor_key') or info.get('extractor') or '').lower()
    return 'youtube' in extractor


def _cookies_available() -> bool:
    return bool(COOKIES_FILE) and os.path.isfile(COOKIES_FILE)


def _common_opts() -> dict:
    return {
        'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best',
        'noplaylist': True,
        'nocheckcertificate': True,
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'source_address': '0.0.0.0',
    }


def _soundcloud_flat_opts() -> dict:
    return {
        **_common_opts(),
        'extract_flat': 'in_playlist',
        'default_search': 'scsearch',
        'ignoreerrors': True,
    }


def _soundcloud_strict_opts() -> dict:
    return {
        **_common_opts(),
        'default_search': 'scsearch',
        'ignoreerrors': False,
    }


def _youtube_flat_opts() -> dict:
    opts = {
        **_common_opts(),
        'extract_flat': 'in_playlist',
        'default_search': 'ytsearch',
        'ignoreerrors': True,
        'extractor_args': {'youtube': {'player_client': ['ios', 'android', 'web']}},
    }
    if _cookies_available():
        opts['cookiefile'] = COOKIES_FILE
    return opts


def _youtube_strict_opts() -> dict:
    opts = {
        **_common_opts(),
        'default_search': 'ytsearch',
        'ignoreerrors': False,
        'extractor_args': {'youtube': {'player_client': ['ios', 'android', 'web']}},
    }
    if _cookies_available():
        opts['cookiefile'] = COOKIES_FILE
    return opts


def _generic_strict_opts() -> dict:
    return {
        **_common_opts(),
        'ignoreerrors': False,
    }


def _opts_for_url(url: str) -> dict:
    if _YT_RE.match(url):
        return _youtube_strict_opts()
    return _generic_strict_opts()


FFMPEG_OPTS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}


class LoopMode(Enum):
    OFF = 0
    SONG = 1
    QUEUE = 2

    def next(self) -> 'LoopMode':
        modes = list(LoopMode)
        return modes[(self.value + 1) % len(modes)]

    def label(self) -> str:
        return {
            LoopMode.OFF: "Sin loop",
            LoopMode.SONG: "Loop: canción",
            LoopMode.QUEUE: "Loop: cola",
        }[self]


@dataclass
class SongInfo:
    title: str
    webpage_url: str
    duration: int
    thumbnail: Optional[str]
    requester: Optional[discord.Member]


def fmt_duration(seconds: int) -> str:
    m, s = divmod(max(0, seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def progress_bar(elapsed: int, total: int, width: int = 12) -> str:
    if total <= 0:
        return "░" * width + " --:-- / --:--"
    clamped = min(elapsed, total)
    filled = int(width * clamped / total)
    return "▓" * filled + "░" * (width - filled) + f" {fmt_duration(clamped)} / {fmt_duration(total)}"


def _song_from_info(info: dict, fallback_url: str) -> SongInfo:
    return SongInfo(
        title=info.get('title') or 'Desconocido',
        webpage_url=info.get('webpage_url') or info.get('url') or fallback_url,
        duration=int(info.get('duration') or 0),
        thumbnail=info.get('thumbnail'),
        requester=None,
    )


def _candidate_url(entry: dict) -> Optional[str]:
    return entry.get('webpage_url') or entry.get('url')


def _flat_search(query: str, opts: dict) -> list[dict]:
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
    if not info:
        return []
    entries = info.get('entries')
    if entries is not None:
        return [e for e in entries if e]
    return [info]


def _extract_full(url: str, opts: dict) -> Optional[dict]:
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _friendly_message(url_or_query: str, err: Exception) -> str:
    msg = str(err)
    low = msg.lower()
    if 'sign in' in low or "confirm you're not a bot" in low or "confirm you're not a robot" in low:
        return (
            "YouTube bloqueó la request. Las cookies pueden haber expirado — "
            "contacta al admin del bot."
        )
    if 'soundcloud' in low and '404' in low:
        return (
            "SoundCloud devolvió 404 para esa pista. Puede estar privada, eliminada "
            "o protegida con DRM (Go+) y no se puede reproducir."
        )
    if '404' in low:
        return "El servidor respondió 404. La pista puede estar privada o eliminada."
    return "No se pudo obtener la información de esa URL."


def _resolve_soundcloud_search(query: str) -> Optional[SongInfo]:
    """Multi-candidate SoundCloud search. Skips DRM-blocked tracks individually."""
    try:
        candidates = _flat_search(f"scsearch10:{query}", _soundcloud_flat_opts())
    except Exception as e:  # noqa: BLE001 — flat search is best-effort
        log.warning("soundcloud: búsqueda flat falló: %s", e)
        return None

    if not candidates:
        log.info("soundcloud: sin candidatos para '%s'", query)
        return None

    strict_opts = _soundcloud_strict_opts()
    for idx, entry in enumerate(candidates, start=1):
        url = _candidate_url(entry)
        if not url:
            continue
        hint = entry.get('title') or url
        try:
            info = _extract_full(url, strict_opts)
        except yt_dlp.utils.DownloadError as e:
            log.info("soundcloud: candidato %d (%s) descartado: %s", idx, hint, e)
            continue
        except yt_dlp.utils.ExtractorError as e:
            log.info("soundcloud: candidato %d (%s) error extractor: %s", idx, hint, e)
            continue
        if not info:
            continue
        log.info("soundcloud: candidato %d (%s) reproducible", idx, hint)
        return _song_from_info(info, url)

    log.info("soundcloud: %d candidatos agotados sin éxito para '%s'", len(candidates), query)
    return None


def _resolve_youtube_search(query: str) -> Optional[SongInfo]:
    """Multi-candidate YouTube search. Skipped entirely if cookies are not configured."""
    if not _cookies_available():
        log.info("youtube: cookies no encontradas en %s, fuente desactivada", COOKIES_FILE)
        return None

    try:
        candidates = _flat_search(f"ytsearch5:{query}", _youtube_flat_opts())
    except Exception as e:  # noqa: BLE001
        log.warning("youtube: búsqueda flat falló: %s", e)
        return None

    if not candidates:
        log.info("youtube: sin candidatos para '%s'", query)
        return None

    strict_opts = _youtube_strict_opts()
    for idx, entry in enumerate(candidates, start=1):
        url = _candidate_url(entry)
        if not url:
            continue
        hint = entry.get('title') or url
        try:
            info = _extract_full(url, strict_opts)
        except yt_dlp.utils.DownloadError as e:
            log.info("youtube: candidato %d (%s) descartado: %s", idx, hint, e)
            continue
        except yt_dlp.utils.ExtractorError as e:
            log.info("youtube: candidato %d (%s) error extractor: %s", idx, hint, e)
            continue
        if not info:
            continue
        log.info("youtube: candidato %d (%s) reproducible", idx, hint)
        return _song_from_info(info, url)

    log.info("youtube: %d candidatos agotados sin éxito para '%s'", len(candidates), query)
    return None


def _resolve_direct_url(url: str) -> SongInfo:
    """Extract metadata for a direct URL. Raises MediaFetchError on failure, or
    YouTubeNotAllowed for YouTube URLs without cookies configured."""
    if _YT_RE.match(url) and not _cookies_available():
        raise YouTubeNotAllowed(
            "YouTube no está disponible desde este servidor. "
            "Usa SoundCloud o configura cookies para yt-dlp."
        )

    opts = _opts_for_url(url)
    try:
        info = _extract_full(url, opts)
    except yt_dlp.utils.DownloadError as e:
        log.warning("URL directa %s falló: %s", url, e)
        raise MediaFetchError(_friendly_message(url, e)) from e
    except yt_dlp.utils.ExtractorError as e:
        log.warning("URL directa %s error extractor: %s", url, e)
        raise MediaFetchError(_friendly_message(url, e)) from e

    if not info:
        raise MediaFetchError("No se pudo obtener la información de esa URL.")
    if 'entries' in info:
        entries = [e for e in info['entries'] if e]
        if not entries:
            raise MediaFetchError("No se pudo obtener la información de esa URL.")
        info = entries[0]

    if _is_youtube_info(info) and not _cookies_available():
        raise YouTubeNotAllowed(
            "YouTube no está disponible desde este servidor. "
            "Usa SoundCloud o configura cookies para yt-dlp."
        )

    return _song_from_info(info, url)


async def fetch_song(query: str) -> Optional[SongInfo]:
    """Extract song metadata for a search query or URL.

    For URLs: extract directly (no fallback — a direct URL is a deliberate choice).
    For text: chain SoundCloud (multi-candidate, skipping DRM tracks) → YouTube
    (only if cookies are configured).
    """
    q = query.strip()
    loop = asyncio.get_running_loop()

    if _URL_RE.match(q):
        return await loop.run_in_executor(None, _resolve_direct_url, q)

    def _resolve_chain() -> Optional[SongInfo]:
        song = _resolve_soundcloud_search(q)
        if song:
            return song
        return _resolve_youtube_search(q)

    song = await loop.run_in_executor(None, _resolve_chain)
    if song:
        return song

    raise MediaFetchError(
        f"No encontré una versión reproducible de '{q}' en ninguna fuente."
    )


async def fetch_stream_url(webpage_url: str) -> Optional[str]:
    """Re-extract a fresh direct audio stream URL just before playing.

    Returns None on any failure (DRM detected late, expired URL, network blip) so
    MusicPlayer can skip this track and advance to the next one in the queue
    instead of crashing.
    """
    def _extract() -> Optional[str]:
        opts = _opts_for_url(webpage_url)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(webpage_url, download=False)
        except yt_dlp.utils.DownloadError as e:
            log.warning("fetch_stream_url: DownloadError para %s: %s", webpage_url, e)
            return None
        except yt_dlp.utils.ExtractorError as e:
            log.warning("fetch_stream_url: ExtractorError para %s: %s", webpage_url, e)
            return None

        if not info:
            return None
        if 'entries' in info:
            entries = [e for e in info['entries'] if e]
            info = entries[0] if entries else None
        if not info:
            return None
        if _is_youtube_info(info) and not _cookies_available():
            log.warning("fetch_stream_url: info de YouTube sin cookies, no se puede reproducir")
            return None
        formats = info.get('formats', [])
        audio_only = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
        if audio_only:
            return audio_only[-1].get('url')
        return info.get('url')

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _extract)


class MusicPlayer:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue: list[SongInfo] = []
        self.current: Optional[SongInfo] = None
        self.voice_client: Optional[discord.VoiceClient] = None
        self.volume: float = 0.5
        self.loop_mode: LoopMode = LoopMode.OFF
        self.text_channel: Optional[discord.abc.Messageable] = None
        self._play_start: float = 0.0
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def is_active(self) -> bool:
        return self.voice_client is not None and (
            self.voice_client.is_playing() or self.voice_client.is_paused()
        )

    def elapsed(self) -> int:
        return int(time.monotonic() - self._play_start) if self._play_start else 0

    def _after(self, error: Optional[Exception]) -> None:
        if error:
            log.error("Error en reproducción guild %s: %s", self.guild_id, error)
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._advance(), self._loop)

    async def _advance(self) -> None:
        if self.loop_mode == LoopMode.SONG and self.current:
            self.queue.insert(0, self.current)
        elif self.loop_mode == LoopMode.QUEUE and self.current:
            self.queue.append(self.current)

        if not self.queue:
            self.current = None
            if self.text_channel:
                embed = discord.Embed(
                    description="✅ Cola vacía. El bot ha salido del canal.",
                    color=EMBED_COLOR,
                )
                try:
                    await self.text_channel.send(embed=embed)
                except Exception:
                    pass
            if self.voice_client and self.voice_client.is_connected():
                await self.voice_client.disconnect()
                self.voice_client = None
            return

        self.current = self.queue.pop(0)
        await self._play_current()

    async def _play_current(self) -> None:
        if not self.voice_client or not self.voice_client.is_connected():
            return

        stream_url = await fetch_stream_url(self.current.webpage_url)
        if not stream_url:
            if self.text_channel:
                try:
                    await self.text_channel.send(embed=discord.Embed(
                        description=f"❌ No se pudo reproducir **{self.current.title}**. Saltando...",
                        color=EMBED_COLOR,
                    ))
                except Exception:
                    pass
            await self._advance()
            return

        pcm = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTS)
        source = discord.PCMVolumeTransformer(pcm, volume=self.volume)
        self._play_start = time.monotonic()
        self._loop = asyncio.get_running_loop()
        self.voice_client.play(source, after=self._after)

        if self.text_channel:
            try:
                await self.text_channel.send(embed=self.now_playing_embed())
            except Exception:
                pass

    async def play(
        self,
        voice_channel: discord.VoiceChannel,
        song: SongInfo,
        text_channel: discord.abc.Messageable,
    ) -> bool:
        """Add song and start playing if idle. Returns True if started, False if queued."""
        self.text_channel = text_channel

        if not self.voice_client or not self.voice_client.is_connected():
            self.voice_client = await voice_channel.connect()

        if self.is_active():
            self.queue.append(song)
            return False

        self.current = song
        await self._play_current()
        return True

    def now_playing_embed(self) -> discord.Embed:
        song = self.current
        if not song:
            return discord.Embed(description="No hay nada reproduciéndose.", color=EMBED_COLOR)
        elapsed = self.elapsed()
        embed = discord.Embed(
            title="🎵 Reproduciendo ahora",
            description=f"**[{song.title}]({song.webpage_url})**",
            color=EMBED_COLOR,
        )
        embed.add_field(
            name="Progreso",
            value=f"`{progress_bar(elapsed, song.duration)}`",
            inline=False,
        )
        embed.add_field(name="Duración", value=fmt_duration(song.duration), inline=True)
        embed.add_field(name="Loop", value=self.loop_mode.label(), inline=True)
        embed.add_field(name="Volumen", value=f"{int(self.volume * 100)}%", inline=True)
        if song.requester:
            embed.set_footer(
                text=f"Pedido por {song.requester.display_name}",
                icon_url=song.requester.display_avatar.url,
            )
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)
        return embed

    async def cleanup(self) -> None:
        self.queue.clear()
        self.current = None
        self._play_start = 0.0
        if self.voice_client:
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()
            if self.voice_client.is_connected():
                await self.voice_client.disconnect()
            self.voice_client = None


_players: dict[int, MusicPlayer] = {}


def get_player(guild_id: int) -> MusicPlayer:
    if guild_id not in _players:
        _players[guild_id] = MusicPlayer(guild_id)
    return _players[guild_id]


def remove_player(guild_id: int) -> None:
    _players.pop(guild_id, None)
