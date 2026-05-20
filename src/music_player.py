import asyncio
import logging
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import discord
import yt_dlp

log = logging.getLogger(__name__)

EMBED_COLOR = 0x8B00FF

_COOKIES_PATH = '/opt/bot-discord-purg/cookies.txt'

YTDL_OPTS = {
    'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best',
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'quiet': True,
    'no_warnings': False,
    'default_search': 'scsearch',
    'source_address': '0.0.0.0',
    'extractor_args': {'youtube': {'player_client': ['ios', 'android', 'web']}},
}

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


def _build_ytdl_opts() -> dict:
    opts = dict(YTDL_OPTS)
    if os.path.isfile(_COOKIES_PATH):
        opts['cookiefile'] = _COOKIES_PATH
    return opts


def _resolve_query(query: str) -> str:
    q = query.strip()
    if _YT_RE.match(q):
        return q
    if _URL_RE.match(q):
        return q
    return f"scsearch:{q}"

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


async def fetch_song(query: str) -> Optional[SongInfo]:
    """Extract song metadata (no stream URL) for a search query or URL."""
    effective = _resolve_query(query)  # raises YouTubeNotAllowed if needed

    def _extract():
        opts = {**_build_ytdl_opts(), 'skip_download': True}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(effective, download=False)
                if not info:
                    return None
                if 'entries' in info:
                    entries = [e for e in info['entries'] if e]
                    if not entries:
                        return None
                    info = None
                    for entry in entries:
                        url = entry.get('url') or entry.get('webpage_url')
                        if not url:
                            continue
                        try:
                            candidate = ydl.extract_info(url, download=False)
                        except yt_dlp.utils.DownloadError:
                            continue
                        except yt_dlp.utils.ExtractorError:
                            continue
                        if candidate and candidate.get('title'):
                            info = candidate
                            break
                    if not info:
                        return None
                return info
        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            if "Sign in" in msg or "bot" in msg.lower():
                raise MediaFetchError(
                    "❌ YouTube bloqueó la request. Las cookies pueden haber expirado — contacta al admin del bot."
                ) from e
            if "soundcloud" in msg.lower() and "404" in msg:
                if _URL_RE.match(effective) and "soundcloud.com" in effective:
                    raise MediaFetchError(
                        "SoundCloud respondio 404. La pista puede estar privada o eliminada."
                    ) from e
                raise MediaFetchError(
                    "SoundCloud respondio 404. La pista puede estar privada o eliminada."
                ) from e
            raise MediaFetchError("No se pudo obtener la informacion de esa URL.") from e
        except yt_dlp.utils.ExtractorError as e:
            msg = str(e)
            if "soundcloud" in msg.lower() and "404" in msg:
                raise MediaFetchError(
                    "SoundCloud respondio 404. La pista puede estar privada o eliminada."
                ) from e
            raise MediaFetchError("No se pudo obtener la informacion de esa URL.") from e

    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, _extract)
    if not info:
        return None
    if _is_youtube_info(info):
        raise YouTubeNotAllowed(
            "YouTube no esta disponible desde este servidor. "
            "Usa SoundCloud o configura cookies para yt-dlp."
        )
    return SongInfo(
        title=info.get('title', 'Desconocido'),
        webpage_url=info.get('webpage_url') or info.get('url', query),
        duration=int(info.get('duration') or 0),
        thumbnail=info.get('thumbnail'),
        requester=None,
    )


async def fetch_stream_url(webpage_url: str) -> Optional[str]:
    """Get a fresh direct audio stream URL just before playing."""
    def _extract():
        opts = {**_build_ytdl_opts(), 'skip_download': True}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(webpage_url, download=False)
                if not info:
                    return None
                if 'entries' in info:
                    entries = [e for e in info['entries'] if e]
                    info = entries[0] if entries else None
                if not info:
                    return None
                if _is_youtube_info(info):
                    return None
                formats = info.get('formats', [])
                audio_only = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
                if audio_only:
                    return audio_only[-1].get('url')
                return info.get('url')
        except yt_dlp.utils.DownloadError as e:
            log.warning("yt-dlp DownloadError en stream: %s", e)
            return None
        except yt_dlp.utils.ExtractorError as e:
            log.warning("yt-dlp ExtractorError en stream: %s", e)
            return None

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
