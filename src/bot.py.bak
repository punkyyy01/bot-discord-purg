import io
import os
import sys
import re
import regex
import random
import asyncio
import hashlib
import logging
import requests
from datetime import datetime
import boto3
import feedparser
from collections import OrderedDict
from logging.handlers import RotatingFileHandler
from botocore.config import Config
from markov_engine import SimpleMarkov
from meme_generator import render_meme, _try_short_sentence

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from db import (
    init_db,
    close_db,
    set_chat_mode,
    get_chat_settings,
    save_corpus_and_user_message,
    get_corpus_messages,
    get_corpus_messages_filtered,
    count_corpus_messages,
    wipe_corpus,
    save_gif_url,
    get_random_gif,
    count_gif_urls,
    add_youtube_sub,
    remove_youtube_sub,
    list_youtube_subs,
    get_all_youtube_subs,
    update_last_video_id,
    set_youtube_mention_role,
    get_user_messages,
    count_user_messages,
    add_ignored_channel,
    remove_ignored_channel,
    list_ignored_channels,
    is_channel_ignored,
    add_meme_schedule,
    remove_meme_schedule,
    list_meme_schedules,
    get_due_meme_schedules,
    update_meme_last_posted,
    save_image_url,
    get_random_image_url,
    count_image_urls,
    get_random_image_url_excluding,
)

# Cargar variables de entorno
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
ENABLE_MESSAGE_CONTENT = os.getenv("ENABLE_MESSAGE_CONTENT", "true").strip().lower() in ("1", "true", "yes")
GUILD_ID_ENV = os.getenv("GUILD_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Configurar logging
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_PATH = os.path.join(_BASE_DIR, "data", "bot.log")
os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)

_fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
_fh = RotatingFileHandler(_LOG_PATH, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_fh, _sh])
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.WARNING)

log = logging.getLogger(__name__)

if not TOKEN:
    log.critical("Falta DISCORD_TOKEN en .env. Copia .env.example a .env y pon tu token.")
    sys.exit(1)

# Configurar intents
intents = discord.Intents.default()
intents.message_content = ENABLE_MESSAGE_CONTENT

# Define `_CONECTORES_FINALES` at the top of the file to avoid undefined variable errors.
_CONECTORES_FINALES = [
    " y", " o", " con", " pero", " de", " para", " a", " que", " entonces", " como"
]


class _LRUDict(OrderedDict):
    def __init__(self, maxsize: int):
        super().__init__()
        self._maxsize = maxsize

    def get(self, key, default=None):
        if key not in self:
            return default
        self.move_to_end(key)
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self._maxsize:
            self.popitem(last=False)


_markov_cache: _LRUDict = _LRUDict(64)
_message_counter: _LRUDict = _LRUDict(256)
_corpus_insert_counter: _LRUDict = _LRUDict(256)
_user_markov_cache: _LRUDict = _LRUDict(64)
_user_corpus_insert_counter: _LRUDict = _LRUDict(256)
_momo_cooldowns: dict[int, float] = {}
_last_meme_image: dict[int, str] = {}

_GIF_RE = re.compile(r'https?://\S*(tenor\.com|giphy\.com|cdn\.discordapp\.com/attachments/\S*\.gif)\S*', re.IGNORECASE)
_REFEED_MAX_MESSAGES = 20_000

_r2_client = boto3.client(
    "s3",
    endpoint_url=os.getenv("R2_ENDPOINT_URL"),
    aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
    config=Config(signature_version="s3v4"),
    region_name="auto",
)

from groq import AsyncGroq
_groq_client = AsyncGroq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

ALLOWED_ROLE_IDS = {1434103563746803801, 1434103563700666401}


def has_allowed_role(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    return any(role.id in ALLOWED_ROLE_IDS for role in member.roles)


def _note_corpus_insert(guild_id: int, channel_id: int) -> None:
    key = (guild_id, channel_id)
    n = _corpus_insert_counter.get(key, 0) + 1
    if n >= 50:
        _corpus_insert_counter[key] = 0
        _markov_cache.pop(guild_id, None)
    else:
        _corpus_insert_counter[key] = n


def _note_user_corpus_insert(guild_id: int, author_id: int) -> None:
    key = (guild_id, author_id)
    n = _user_corpus_insert_counter.get(key, 0) + 1
    if n >= 50:
        _user_corpus_insert_counter[key] = 0
        _user_markov_cache.pop(key, None)
    else:
        _user_corpus_insert_counter[key] = n


# 1. BOT CUSTOM PARA CIERRE LIMPIO DE BASE DE DATOS
class MyCustomBot(commands.Bot):
    async def close(self):
        log.info("Cerrando conexión a la base de datos...")
        await close_db()
        await super().close()


bot = MyCustomBot(command_prefix="!", intents=intents)
bot.remove_command("help")


# --- UTILIDADES ---
def chunk_message(text: str, max_length: int = 1900) -> list[str]:
    """Divide un texto largo en fragmentos que Discord pueda aceptar, intentando no cortar palabras."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        chunk = text[:max_length]
        last_newline = chunk.rfind('\n')
        last_space = chunk.rfind(' ')
        cut_index = last_newline if last_newline > 0 else (last_space if last_space > 0 else max_length)
        chunks.append(text[:cut_index].strip())
        text = text[cut_index:].strip()
    return chunks


_EMOJI_RE = regex.compile(r'[\p{Extended_Pictographic}\p{Emoji_Component}]+', regex.UNICODE)


# Frases que delatan a la IA "asistente"
def post_process_reply(text: str) -> str:
    if not text:
        return "me quedé en blanco, pregunta de nuevo"

    # Limpieza básica
    text = text.lower().strip()
    text = _EMOJI_RE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text)

    # Filtro de conectores finales
    changed = True
    while changed:
        changed = False
        for con in _CONECTORES_FINALES:
            if text.endswith(con):
                text = text[:-len(con)].strip()
                changed = True

    if text.endswith("."):
        text = text.rstrip(".")

    if not text.strip():
        text = "no sé xd"

    return text.strip()


_ANSI_ESCAPE_RE = re.compile(r'(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]')
_ANSI_BRACKET_RE = re.compile(r'\]\d*;[^\]]*')
_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)
_DISCORD_MENTIONS_RE = re.compile(r'<a?:\w+:\d+>|<@!?\d+>|<#\d+>|<@&\d+>')


def clean_for_corpus(text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return None

    # Eliminar secuencias ANSI y basura típica de logs
    t = _ANSI_ESCAPE_RE.sub(" ", t)
    t = _ANSI_BRACKET_RE.sub(" ", t)
    t = t.replace("[0m", " ").replace("][", " ")

    # Eliminar URLs y menciones Discord
    t = _URL_RE.sub(" ", t)
    t = _DISCORD_MENTIONS_RE.sub(" ", t)
    t = re.sub(r'\b\d{5,}\b', '', t)

    # Eliminar líneas que sean solo números/símbolos/caracteres especiales
    kept_lines: list[str] = []
    for line in t.splitlines():
        s = line.strip()
        if not s:
            continue
        if not any(ch.isalpha() for ch in s):
            continue
        kept_lines.append(s)

    t = " ".join(kept_lines)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    return t


async def build_markov_model(guild_id: int) -> SimpleMarkov | None:
    cached = _markov_cache.get(guild_id)
    if cached is not None:
        return cached

    corpus = await get_corpus_messages(guild_id)
    if len(corpus) < 50:
        return None

    def build() -> SimpleMarkov:
        m = SimpleMarkov()
        m.add_many(corpus)
        return m

    try:
        model = await asyncio.to_thread(build)
    except Exception:
        log.exception("Error construyendo modelo Markov para guild %s", guild_id)
        return None

    _markov_cache[guild_id] = model
    return model


async def generate_markov_reply(guild_id: int) -> str | None:
    model = await build_markov_model(guild_id)
    if not model or model.is_empty:
        return None

    try:
        sentence = await asyncio.to_thread(
            model.generate,
            max_words=20,
            max_attempts=5,
            min_words=1,
        )
    except Exception:
        log.exception("Error generando frase Markov para guild %s", guild_id)
        sentence = None

    return sentence


async def generate_markov_for_user(guild_id: int, author_id: int) -> str | None:
    key = (guild_id, author_id)
    model = _user_markov_cache.get(key)
    if model is None:
        corpus = await get_user_messages(guild_id, author_id)
        if len(corpus) < 30:
            return None

        def build() -> SimpleMarkov:
            m = SimpleMarkov()
            m.add_many(corpus)
            return m

        try:
            model = await asyncio.to_thread(build)
        except Exception:
            log.exception("Error construyendo modelo Markov para usuario %s", author_id)
            return None
        _user_markov_cache[key] = model

    try:
        sentence = await asyncio.to_thread(
            model.generate,
            max_words=20,
            max_attempts=5,
            min_words=1,
        )
    except Exception:
        log.exception("Error generando frase Markov para usuario %s", author_id)
        sentence = None
    return sentence


def upload_gif_to_r2_sync(url: str, guild_id: int) -> str | None:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; bot)"}
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            log.error("HTTP %s al descargar GIF para R2: %s", resp.status_code, url)
            return None
        data = resp.content
        key = f"{guild_id}/{hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()}.gif"
        _r2_client.put_object(
            Bucket=os.getenv("R2_BUCKET_NAME", ""),
            Key=key,
            Body=data,
            ContentType="image/gif",
        )
        return f"{os.getenv('R2_PUBLIC_URL', '').rstrip('/')}/{key}"
    except Exception:
        log.exception("Error subiendo GIF a R2: %s", url)
        return None


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_MEME_MAX_BYTES = 10 * 1024 * 1024


async def handle_meme_command(message: discord.Message) -> None:
    log.info(
        "handle_meme_command: message_id=%s content=%r has_reference=%s ref_message_id=%s",
        message.id,
        message.content,
        message.reference is not None,
        message.reference.message_id if message.reference else None,
    )

    if not message.reference or not message.reference.message_id:
        log.info("handle_meme_command: no es reply, ignorando")
        return

    ref = message.reference.resolved
    if ref is None or isinstance(ref, discord.DeletedReferencedMessage):
        log.info("handle_meme_command: ref no resuelto, haciendo fetch de message_id=%s", message.reference.message_id)
        try:
            ref = await message.channel.fetch_message(message.reference.message_id)
        except Exception:
            log.info("handle_meme_command: fetch del mensaje referenciado falló, ignorando")
            return
    if not isinstance(ref, discord.Message):
        log.info("handle_meme_command: ref es %s (no es Message válido), ignorando", type(ref).__name__)
        return

    log.info("handle_meme_command: ref resuelto OK, message_id=%s, attachments=%d", ref.id, len(ref.attachments))

    image_att = next(
        (a for a in ref.attachments if os.path.splitext(a.filename.lower())[1] in _IMAGE_EXTS),
        None,
    )
    if image_att is None:
        log.info("handle_meme_command: no se encontró attachment de imagen en el mensaje referenciado")
        await message.reply("necesito una imagen pa generar el momo")
        return

    log.info("handle_meme_command: imagen encontrada filename=%r size=%d bytes", image_att.filename, image_att.size)

    if image_att.size > _MEME_MAX_BYTES:
        await message.reply("la imagen pesa mucho")
        return

    try:
        img_bytes = await image_att.read()
    except Exception:
        log.exception("Error descargando imagen para meme")
        await message.reply("se rompio algo")
        return

    if not message.guild:
        log.info("handle_meme_command: mensaje fuera de guild, no hay modelo Markov disponible")
        await message.reply("no me salio nada, intenta de nuevo")
        return

    model = await build_markov_model(message.guild.id)
    if not model or model.is_empty:
        log.info("handle_meme_command: modelo Markov no disponible (model=%s is_empty=%s)", model, model.is_empty if model else "N/A")
        await message.reply("no me salio nada, intenta de nuevo")
        return

    log.info("handle_meme_command: modelo Markov OK, generando texto")

    text = await asyncio.to_thread(_try_short_sentence, model)
    log.info("handle_meme_command: texto generado=%r", text)
    if not text:
        await message.reply("no me salio nada, intenta de nuevo")
        return

    try:
        meme_bytes = await asyncio.to_thread(render_meme, img_bytes, text)
    except Exception:
        log.exception("Error generando meme con Pillow")
        await message.reply("se rompio algo")
        return

    await message.reply(file=discord.File(io.BytesIO(meme_bytes), filename="meme.png"))


# --- EVENTOS PRINCIPALES ---
async def get_latest_video(youtube_channel_id: str) -> dict | None:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={youtube_channel_id}"
    try:
        feed = await asyncio.to_thread(feedparser.parse, url)
        if not feed.entries:
            return None
        entry = feed.entries[0]
        video_id = getattr(entry, "yt_videoid", None) or entry.get("id", "").split(":")[-1]
        if not video_id:
            return None
        return {
            "id": video_id,
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "author": entry.get("author", ""),
        }
    except Exception:
        log.exception("Error obteniendo RSS para canal YouTube %s", youtube_channel_id)
        return None


@tasks.loop(minutes=15)
async def check_youtube():
    subs = await get_all_youtube_subs()

    async def _check_one(sub: dict) -> None:
        try:
            video = await get_latest_video(sub["youtube_channel_id"])
            if video is None:
                return
            if video["id"] != sub["last_video_id"]:
                channel = bot.get_channel(sub["discord_channel_id"])
                if channel and isinstance(channel, discord.TextChannel):
                    mention = ""
                    if sub.get("mention_role_id"):
                        mention = f"<@&{sub['mention_role_id']}> "
                    await channel.send(
                        f"{mention}📺 **{video['author']}** subió un video nuevo!\n"
                        f"**{video['title']}**\n{video['url']}"
                    )
                    await update_last_video_id(sub["guild_id"], sub["youtube_channel_id"], video["id"])
        except Exception:
            log.exception("Error procesando suscripción YouTube %s", sub["youtube_channel_id"])

    await asyncio.gather(*(_check_one(sub) for sub in subs))


async def generate_groq_meme_caption(
    image_bytes: bytes,
    corpus_sample: list[str],
) -> str | None:
    """
    Llama a Groq Vision para generar un caption de meme.
    Paso 1: describe la imagen.
    Paso 2: genera el caption usando la descripción + muestra del corpus.
    Retorna el caption o None si falla.
    """
    if not _groq_client:
        log.warning("GROQ_API_KEY no configurada")
        return None

    import base64
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    # Paso 1: describir la imagen
    try:
        vision_response = await _groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}"
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Describe this image in detail in SPANISH. Include: "
                                "what you see, any visible text, the mood, what's absurd "
                                "or funny about it, and any cultural context you recognize. "
                                "2-3 sentences max. Be direct, no preamble."
                            ),
                        },
                    ],
                }
            ],
            max_tokens=150,
        )
        image_description = vision_response.choices[0].message.content.strip()
    except Exception:
        log.exception("Error en Groq Vision paso 1 (descripción)")
        return None

    # Paso 2: generar caption con contexto del server
    corpus_text = "\n".join(f"- {msg}" for msg in corpus_sample[:100])
    system_prompt = (
        "Eres un generador de captions para memes de un servidor de Discord "
        "donde todos hablan español. "
        "Tu trabajo es escribir un caption corto que combine: "
        "1) algo gracioso, absurdo o específico de la imagen "
        "2) el vocabulario y humor exacto de los miembros del servidor. "
        "El caption tiene que sonar como si uno de ellos lo hubiera escrito, "
        "no como un chiste genérico de Facebook. "
        "Sé irreverente, caótico, sin filtro. "
        "Máximo 80 caracteres. Responde SOLO con el caption, nada más."
    )
    user_prompt = (
        f"La imagen muestra: {image_description}\n\n"
        f"Estos son mensajes REALES de los miembros del servidor "
        f"(usa su vocabulario y humor):\n"
        f"{corpus_text}\n\n"
        f"Escribe UN caption (máx 80 caracteres) que conecte algo de la imagen "
        f"con cómo habla esta gente. Que dé risa. Solo el caption."
    )

    try:
        caption_response = await _groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=80,
            temperature=0.9,
        )
        caption = caption_response.choices[0].message.content.strip()
        caption = caption.strip('"').strip("'").strip()
        if len(caption) > 80:
            caption = caption[:80].rsplit(" ", 1)[0].strip()
        return caption if caption else None
    except Exception as e:
        if "429" in str(e) or "rate_limit" in str(e).lower():
            log.warning("Groq rate limit alcanzado, fallback a Markov")
            return None
        log.exception("Error en Groq Vision paso 2 (caption)")
        return None


@tasks.loop(minutes=10)
async def auto_meme_task():
    schedules = await get_due_meme_schedules()
    for schedule in schedules:
        try:
            channel = bot.get_channel(schedule["channel_id"])
            if not channel or not isinstance(channel, discord.TextChannel):
                continue

            # Obtener imagen random del pool del server (sin repetir la anterior)
            guild_id = schedule["guild_id"]
            last = _last_meme_image.get(guild_id)
            image_url = await get_random_image_url_excluding(guild_id, exclude_url=last)
            if image_url:
                _last_meme_image[guild_id] = image_url
            if not image_url:
                log.info(
                    "auto_meme: sin imágenes en pool para guild %s",
                    guild_id
                )
                continue

            # Descargar imagen
            try:
                img_resp = await asyncio.to_thread(
                    __import__("requests").get,
                    image_url,
                    timeout=15,
                )
                if img_resp.status_code != 200:
                    log.warning("auto_meme: no se pudo descargar imagen %s", image_url)
                    continue
                img_bytes = img_resp.content
            except Exception:
                log.exception("auto_meme: error descargando imagen %s", image_url)
                continue

            if len(img_bytes) > _MEME_MAX_BYTES:
                continue

            # Obtener muestra del corpus
            corpus_sample = await get_corpus_messages_filtered(
                guild_id, min_words=5, limit=400
            )
            if not corpus_sample:
                log.info(
                    "auto_meme: corpus vacío para guild %s",
                    guild_id
                )
                continue

            # Generar caption con Groq
            if _groq_client:
                caption = await generate_groq_meme_caption(img_bytes, corpus_sample)
            else:
                # Fallback a Markov si no hay Groq configurado
                model = await build_markov_model(guild_id)
                caption = await asyncio.to_thread(
                    _try_short_sentence, model
                ) if model and not model.is_empty else None

            if not caption:
                log.info("auto_meme: no se generó caption para guild %s", guild_id)
                continue

            # Renderizar y postear
            meme_bytes = await asyncio.to_thread(render_meme, img_bytes, caption)
            await channel.send(
                file=discord.File(io.BytesIO(meme_bytes), filename="meme.png")
            )
            await update_meme_last_posted(
                guild_id, schedule["channel_id"]
            )
            log.info(
                "auto_meme: meme posteado en canal %s con caption: %s",
                schedule["channel_id"], caption
            )

        except Exception:
            log.exception(
                "auto_meme: error inesperado para canal %s",
                schedule["channel_id"]
            )


@bot.event
async def on_ready():
    await init_db()
    if not check_youtube.is_running():
        check_youtube.start()
    if not auto_meme_task.is_running():
        auto_meme_task.start()

    try:
        log.info("Iniciando sincronización de comandos")

        if GUILD_ID_ENV:
            # Sync instantáneo a un servidor específico (desarrollo)
            guild_obj = discord.Object(id=int(GUILD_ID_ENV))
            bot.tree.copy_global_to(guild=guild_obj)
            synced = await bot.tree.sync(guild=guild_obj)
            log.info("Sync al servidor %s: %s", GUILD_ID_ENV, [c.name for c in synced])
        else:
            # Sync global (puede tardar hasta 1 hora en propagarse)
            synced = await bot.tree.sync()
            log.info("Sync global: %s", [c.name for c in synced])

    except Exception:
        log.exception("Error en la sincronización de comandos")

    log.info("Bot listo como %s", bot.user)


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ No tienes permisos para usar este comando.")
        return
    elif isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Faltan argumentos. Revisa cómo usar el comando.")
        return
    log.error("Error en comando %s", getattr(ctx, "command", None), exc_info=error)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if (message.content or "").strip().lower() == "artemis generar":
        await handle_meme_command(message)
        return

    # 1. Procesar comandos básicos (!ping, !chat)
    await bot.process_commands(message)
    if (message.content or "").strip().startswith("!"):
        return

    auto_generate = False

    if message.guild:
        if await is_channel_ignored(message.guild.id, message.channel.id):
            return

        # Guardar GIFs (tenor/giphy) si aparecen en el mensaje
        if message.content:
            for m in _GIF_RE.finditer(message.content):
                try:
                    url = m.group(0)
                    if "cdn.discordapp.com" in url:
                        r2_url = await asyncio.to_thread(upload_gif_to_r2_sync, url, message.guild.id)
                        if r2_url:
                            url = r2_url
                    await save_gif_url(message.guild.id, url)
                except Exception:
                    log.exception("Error guardando GIF de mensaje: %s", m.group(0))

        for attachment in message.attachments:
            if attachment.url and (
                attachment.url.lower().endswith('.gif') or
                (attachment.content_type and 'gif' in attachment.content_type)
            ):
                try:
                    url = attachment.url
                    if "cdn.discordapp.com" in url:
                        r2_url = await asyncio.to_thread(upload_gif_to_r2_sync, url, message.guild.id)
                        if r2_url:
                            url = r2_url
                    await save_gif_url(message.guild.id, url)
                except Exception:
                    log.exception("Error guardando GIF adjunto: %s", attachment.url)

        for attachment in message.attachments:
            ext = os.path.splitext(attachment.filename.lower())[1]
            if ext in {".png", ".jpg", ".jpeg", ".webp"} and attachment.size <= _MEME_MAX_BYTES:
                try:
                    await save_image_url(message.guild.id, attachment.url)
                except Exception:
                    log.exception("Error guardando imagen en corpus: %s", attachment.url)

        cleaned = clean_for_corpus(message.content or "")
        inserted = False
        if cleaned is not None:
            inserted, user_inserted = await save_corpus_and_user_message(
                message.guild.id, message.channel.id,
                message.author.id, message.author.display_name, cleaned,
                message_id=message.id,
            )
            if user_inserted:
                _note_user_corpus_insert(message.guild.id, message.author.id)
        if inserted:
            _note_corpus_insert(message.guild.id, message.channel.id)
            key = (message.guild.id, message.channel.id)
            _message_counter[key] = _message_counter.get(key, 0) + 1
            if _message_counter[key] >= 15:
                _message_counter[key] = 0
                auto_generate = True

        # Reacción aleatoria con emoji custom del server
        if random.random() < 0.05:
            try:
                emojis = list(getattr(message.guild, "emojis", []) or [])
                if emojis:
                    await message.add_reaction(random.choice(emojis))
            except Exception:
                log.exception("Error añadiendo reacción emoji")

    # 2. Verificar si el bot fue mencionado o si le respondieron a él directamente
    mention_bot = bool(bot.user and bot.user.id in (message.raw_mentions or []))
    reply_to_bot = False
    if message.reference and message.reference.message_id and bot.user:
        ref_msg = message.reference.resolved
        if isinstance(ref_msg, discord.Message):
            reply_to_bot = ref_msg.author.id == bot.user.id

    if not (mention_bot or reply_to_bot):
        if message.guild and auto_generate:
            try:
                if random.random() < 0.45:
                    gif_url = await get_random_gif(message.guild.id)
                    if gif_url:
                        await message.channel.send(gif_url)
                        return
                reply = await generate_markov_reply(message.guild.id)
                if reply is not None:
                    reply = post_process_reply(reply)
                    for chunk in chunk_message(reply):
                        await message.channel.send(chunk)
            except Exception:
                log.exception("Error en generación automática de respuesta")
        return

    if not message.guild:
        return

    # 3. Respetar restricciones de canal y modo de chat
    settings = await get_chat_settings(message.guild.id)
    if not settings["enabled"]:
        return
    if settings["channel_id"] and message.channel.id != settings["channel_id"]:
        return

    if random.random() < 0.45:
        gif_url = await get_random_gif(message.guild.id)
        if gif_url:
            await message.reply(gif_url)
            return

    reply = await generate_markov_reply(message.guild.id)
    reply = post_process_reply(reply) if reply else "..."
    for chunk in chunk_message(reply):
        await message.reply(chunk)


# --- COMANDOS BÁSICOS ---
@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("Pong!")


# --- SLASH COMMANDS ---
@bot.tree.command(name="refeed", description="Guarda los últimos mensajes del canal en el corpus del modelo Markov.")
async def refeed_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return

    if not has_allowed_role(interaction):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    channel = interaction.channel
    if not isinstance(channel, discord.abc.Messageable):
        await interaction.followup.send("No puedo leer el historial de este canal.")
        return

    if await is_channel_ignored(interaction.guild.id, channel.id):
        await interaction.followup.send("⚠️ Este canal está en la lista de ignorados. Usa `/corpus_ignorar quitar` primero si querés incluirlo.")
        return

    saved = 0
    fetched = 0

    last_msg_id: int | None = None
    while fetched < _REFEED_MAX_MESSAGES:
        before_obj = discord.Object(id=last_msg_id) if last_msg_id else None
        try:
            batch = [msg async for msg in channel.history(limit=100, before=before_obj, oldest_first=False)]
        except discord.Forbidden:
            await interaction.followup.send("❌ Sin permisos para leer el historial de este canal.")
            return
        if not batch:
            break
        fetched += len(batch)

        for msg in batch:
            if msg.author.bot:
                continue

            if msg.content:
                for m in _GIF_RE.finditer(msg.content):
                    try:
                        url = m.group(0)
                        if "cdn.discordapp.com" in url:
                            r2_url = await asyncio.to_thread(upload_gif_to_r2_sync, url, interaction.guild.id)
                            if r2_url:
                                url = r2_url
                        await save_gif_url(interaction.guild.id, url)
                    except Exception:
                        log.exception("Error procesando GIF en refeed: %s", m.group(0))

            for attachment in msg.attachments:
                if attachment.url and (
                    attachment.url.lower().endswith('.gif') or
                    (attachment.content_type and 'gif' in attachment.content_type)
                ):
                    try:
                        url = attachment.url
                        if "cdn.discordapp.com" in url:
                            r2_url = await asyncio.to_thread(upload_gif_to_r2_sync, url, interaction.guild.id)
                            if r2_url:
                                url = r2_url
                        await save_gif_url(interaction.guild.id, url)
                    except Exception:
                        log.exception("Error procesando GIF adjunto en refeed: %s", attachment.url)

            cleaned = clean_for_corpus(msg.content or "")
            if cleaned is None:
                continue
            corpus_ins, user_ins = await save_corpus_and_user_message(
                interaction.guild.id, msg.channel.id,
                msg.author.id, msg.author.display_name, cleaned,
                message_id=msg.id,
            )
            if corpus_ins:
                saved += 1
                _note_corpus_insert(interaction.guild.id, msg.channel.id)
            if user_ins:
                _note_user_corpus_insert(interaction.guild.id, msg.author.id)

        last_msg_id = batch[-1].id

    result = f"✅ Guardados {saved} mensajes en el corpus."
    if fetched >= _REFEED_MAX_MESSAGES:
        result += f"\n⚠️ Límite de {_REFEED_MAX_MESSAGES:,} mensajes leídos alcanzado; el canal puede tener más."
    await interaction.followup.send(result)


@bot.tree.command(name="refeed_all", description="Guarda mensajes de todos los canales de texto del servidor en el corpus del modelo Markov.")
async def refeed_all_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return

    if not has_allowed_role(interaction):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    me = interaction.guild.me
    if me is None and bot.user is not None:
        me = interaction.guild.get_member(bot.user.id)
    if me is None:
        await interaction.followup.send("No puedo determinar los permisos del bot.")
        return

    total_saved = 0
    total_fetched = 0
    any_channel_hit_limit = False

    for channel in interaction.guild.text_channels:
        perms = channel.permissions_for(me)
        if not (perms.read_messages and perms.read_message_history):
            continue
        if await is_channel_ignored(interaction.guild.id, channel.id):
            continue

        saved = 0
        channel_fetched = 0
        last_msg_id: int | None = None
        while channel_fetched < _REFEED_MAX_MESSAGES:
            before_obj = discord.Object(id=last_msg_id) if last_msg_id else None
            try:
                batch = [msg async for msg in channel.history(limit=100, before=before_obj, oldest_first=False)]
            except discord.Forbidden:
                break
            if not batch:
                break
            batch_len = len(batch)
            channel_fetched += batch_len
            total_fetched += batch_len

            for msg in batch:
                if msg.author.bot:
                    continue

                if msg.content:
                    for m in _GIF_RE.finditer(msg.content):
                        try:
                            url = m.group(0)
                            if "cdn.discordapp.com" in url:
                                r2_url = await asyncio.to_thread(upload_gif_to_r2_sync, url, interaction.guild.id)
                                if r2_url:
                                    url = r2_url
                            await save_gif_url(interaction.guild.id, url)
                        except Exception:
                            log.exception("Error procesando GIF en refeed_all: %s", m.group(0))

                for attachment in msg.attachments:
                    if attachment.url and (
                        attachment.url.lower().endswith('.gif') or
                        (attachment.content_type and 'gif' in attachment.content_type)
                    ):
                        try:
                            url = attachment.url
                            if "cdn.discordapp.com" in url:
                                r2_url = await asyncio.to_thread(upload_gif_to_r2_sync, url, interaction.guild.id)
                                if r2_url:
                                    url = r2_url
                            await save_gif_url(interaction.guild.id, url)
                        except Exception:
                            log.exception("Error procesando GIF adjunto en refeed_all: %s", attachment.url)

                for attachment in msg.attachments:
                    ext = os.path.splitext(attachment.filename.lower())[1]
                    if ext in {".png", ".jpg", ".jpeg", ".webp"} \
                            and attachment.size <= _MEME_MAX_BYTES:
                        try:
                            await save_image_url(interaction.guild.id, attachment.url)
                        except Exception:
                            log.exception(
                                "Error guardando imagen en refeed_all: %s",
                                attachment.url
                            )

                cleaned = clean_for_corpus(msg.content or "")
                if cleaned is None:
                    continue
                corpus_ins, user_ins = await save_corpus_and_user_message(
                    interaction.guild.id, msg.channel.id,
                    msg.author.id, msg.author.display_name, cleaned,
                    message_id=msg.id,
                )
                if corpus_ins:
                    saved += 1
                    _note_corpus_insert(interaction.guild.id, msg.channel.id)
                if user_ins:
                    _note_user_corpus_insert(interaction.guild.id, msg.author.id)

            last_msg_id = batch[-1].id

        if channel_fetched >= _REFEED_MAX_MESSAGES:
            any_channel_hit_limit = True
        total_saved += saved

    result = f"✅ Refeed_all completado. Total guardado: {total_saved} mensajes."
    if any_channel_hit_limit:
        result += f"\n⚠️ Límite de {_REFEED_MAX_MESSAGES:,} mensajes leídos alcanzado; algunos canales pueden estar incompletos."
    await interaction.followup.send(result)


@bot.tree.command(name="generar", description="Genera un mensaje usando el modelo Markov del canal.")
async def generar_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    if interaction.channel is None:
        await interaction.followup.send("No puedo determinar el canal.", ephemeral=True)
        return
    reply = await generate_markov_reply(interaction.guild.id)
    reply = post_process_reply(reply) if reply else "..."
    await interaction.followup.send(reply)


@bot.tree.command(name="chatmode", description="Activa o desactiva las respuestas automáticas del bot al mencionarlo.")
@app_commands.describe(
    estado="Activar o desactivar",
    canal="Canal específico para auto-reply (opcional, por defecto todos)"
)
@app_commands.choices(estado=[
    app_commands.Choice(name="Activar", value="on"),
    app_commands.Choice(name="Desactivar", value="off"),
])
async def chatmode_slash(interaction: discord.Interaction, estado: app_commands.Choice[str], canal: discord.TextChannel | None = None):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ Necesitas el permiso `Gestionar servidor`.", ephemeral=True)
        return

    enabled = estado.value == "on"
    channel_id = canal.id if canal else None
    await set_chat_mode(interaction.guild.id, enabled, channel_id)

    if enabled:
        if canal:
            msg = f"✅ Auto-reply activado solo en {canal.mention}."
        else:
            msg = "✅ Auto-reply activado en todos los canales."
    else:
        msg = "❌ Auto-reply desactivado."

    await interaction.response.send_message(msg)


@bot.tree.command(name="corpus_wipe", description="Borra el corpus del servidor (mensajes) y reinicia el cache Markov.")
async def corpus_wipe_slash(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not interaction.guild:
        await interaction.followup.send("Solo en servidores.", ephemeral=True)
        return

    if not has_allowed_role(interaction):
        await interaction.followup.send("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return

    await wipe_corpus(interaction.guild.id)

    # Limpiar caches por guild
    gid = interaction.guild.id
    _markov_cache.pop(gid, None)
    for key in [k for k in _corpus_insert_counter.keys() if k[0] == gid]:
        _corpus_insert_counter.pop(key, None)
    for key in [k for k in _message_counter.keys() if k[0] == gid]:
        _message_counter.pop(key, None)
    for key in [k for k in _user_corpus_insert_counter.keys() if k[0] == gid]:
        _user_corpus_insert_counter.pop(key, None)

    await interaction.followup.send("🗑️ Corpus limpiado. Corre /refeed_all para repoblarlo.")


@bot.tree.command(name="corpus_info", description="Muestra cuántos mensajes hay en el corpus del canal actual.")
async def corpus_info_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return

    if interaction.channel is None:
        await interaction.response.send_message("No puedo determinar el canal.", ephemeral=True)
        return

    count = await count_corpus_messages(interaction.guild.id, interaction.channel.id)
    msg = f"📊 El corpus de este canal tiene {count} mensajes."
    if count < 50:
        msg += "\n⚠️ Necesita al menos 50 mensajes para generar bien."
    await interaction.response.send_message(msg)


@bot.tree.command(name="gif_add", description="Agrega un GIF a la colección del servidor.")
@app_commands.describe(url="URL del GIF (tenor.com, giphy.com o cdn.discordapp.com)")
async def gif_add_slash(interaction: discord.Interaction, url: str):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
    if not has_allowed_role(interaction):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    url = url.strip()
    if "cdn.discordapp.com" in url:
        final_url = await asyncio.to_thread(upload_gif_to_r2_sync, url, interaction.guild.id)
        if not final_url:
            await interaction.followup.send("❌ No se pudo subir el GIF a R2. Comprueba que la URL sea accesible.")
            return
    elif "tenor.com" in url or "giphy.com" in url:
        final_url = url
    else:
        await interaction.followup.send("❌ URL no reconocida. Solo se aceptan GIFs de tenor.com, giphy.com o cdn.discordapp.com.")
        return

    inserted = await save_gif_url(interaction.guild.id, final_url)
    total = await count_gif_urls(interaction.guild.id)
    if inserted:
        await interaction.followup.send(f"✅ GIF guardado. La colección del servidor tiene {total} GIFs en total.")
    else:
        await interaction.followup.send(f"ℹ️ Ese GIF ya estaba en la colección. Total: {total} GIFs.")


@bot.tree.command(name="youtube_add", description="Suscribe un canal de YouTube para notificaciones en un canal de Discord.")
@app_commands.describe(
    youtube_channel_id="ID del canal de YouTube (empieza con UC...)",
    discord_channel="Canal de Discord donde se avisarán los nuevos videos",
    rol="Rol a mencionar cuando haya un video nuevo (opcional)",
)
async def youtube_add_slash(
    interaction: discord.Interaction,
    youtube_channel_id: str,
    discord_channel: discord.TextChannel,
    rol: discord.Role | None = None,
):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
    if not has_allowed_role(interaction):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    video = await get_latest_video(youtube_channel_id)
    if video is None:
        await interaction.followup.send("❌ No se pudo obtener información del canal. Verifica el ID.")
        return

    channel_name = video["author"] or youtube_channel_id
    channel_id = interaction.channel.id if interaction.channel else 0

    added = await add_youtube_sub(
        interaction.guild.id,
        channel_id,
        youtube_channel_id,
        channel_name,
        discord_channel.id,
        mention_role_id=rol.id if rol else None,
    )

    if added:
        await update_last_video_id(interaction.guild.id, youtube_channel_id, video["id"])
        msg = f"✅ Suscrito al canal **{channel_name}**. Los nuevos videos se avisarán en {discord_channel.mention}."
        if rol:
            msg += f" Se mencionará a {rol.mention}."
        await interaction.followup.send(msg)
    else:
        await interaction.followup.send(f"ℹ️ Ya estás suscrito al canal **{channel_name}**.")


@bot.tree.command(name="youtube_remove", description="Elimina la suscripción a un canal de YouTube.")
@app_commands.describe(youtube_channel_id="ID del canal de YouTube a eliminar")
async def youtube_remove_slash(interaction: discord.Interaction, youtube_channel_id: str):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
    if not has_allowed_role(interaction):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return

    removed = await remove_youtube_sub(interaction.guild.id, youtube_channel_id)
    if removed:
        await interaction.response.send_message(f"✅ Suscripción a `{youtube_channel_id}` eliminada.", ephemeral=True)
    else:
        await interaction.response.send_message(f"ℹ️ No había suscripción activa para `{youtube_channel_id}`.", ephemeral=True)


@bot.tree.command(name="youtube_list", description="Muestra todas las suscripciones de YouTube activas del servidor.")
async def youtube_list_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
    if not has_allowed_role(interaction):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return

    subs = await list_youtube_subs(interaction.guild.id)
    if not subs:
        await interaction.response.send_message("ℹ️ No hay suscripciones de YouTube activas en este servidor.", ephemeral=True)
        return

    lines = []
    for sub in subs:
        dc_channel = interaction.guild.get_channel(sub["discord_channel_id"])
        dc_mention = dc_channel.mention if dc_channel else f"<#{sub['discord_channel_id']}>"
        lines.append(f"• **{sub['youtube_channel_name']}** (`{sub['youtube_channel_id']}`) → {dc_mention}")

    await interaction.response.send_message(
        "**Suscripciones de YouTube activas:**\n" + "\n".join(lines),
        ephemeral=True,
    )


@bot.tree.command(name="youtube_set_mention", description="Configura el rol a mencionar en las notificaciones de un canal de YouTube.")
@app_commands.describe(
    channel_id="ID del canal de YouTube",
    rol="Rol a mencionar (omitir para quitar la mención)",
)
async def youtube_set_mention_slash(
    interaction: discord.Interaction,
    channel_id: str,
    rol: discord.Role | None = None,
):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
    if not has_allowed_role(interaction):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return

    role_id = rol.id if rol else None
    updated = await set_youtube_mention_role(interaction.guild.id, channel_id, role_id)
    if not updated:
        await interaction.response.send_message(f"ℹ️ No se encontró suscripción para `{channel_id}`.", ephemeral=True)
        return

    if rol:
        await interaction.response.send_message(
            f"✅ Las notificaciones de `{channel_id}` mencionarán a {rol.mention}.", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"✅ Mención eliminada de las notificaciones de `{channel_id}`.", ephemeral=True
        )


@bot.tree.command(name="imitar", description="Genera un mensaje imitando el estilo de un usuario del servidor.")
@app_commands.describe(usuario="Usuario a imitar")
async def imitar_slash(interaction: discord.Interaction, usuario: discord.Member):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    count = await count_user_messages(interaction.guild.id, usuario.id)
    if count < 30:
        await interaction.followup.send(
            f"⚠️ **{usuario.display_name}** solo tiene {count} mensaje(s) en el corpus. Necesita al menos 30."
        )
        return

    result = await generate_markov_for_user(interaction.guild.id, usuario.id)
    if result is None:
        await interaction.followup.send(
            f"⚠️ No se pudo generar un mensaje para **{usuario.display_name}**. Intenta más tarde."
        )
        return

    await interaction.followup.send(f'🎭 **{usuario.display_name}** diría: "{result}"')


_corpus_ignorar = app_commands.Group(
    name="corpus_ignorar",
    description="Gestiona los canales que el bot ignora completamente",
)


@_corpus_ignorar.command(name="add", description="Añade un canal a la lista de ignorados.")
@app_commands.describe(canal="Canal que el bot debe ignorar")
async def corpus_ignorar_add(interaction: discord.Interaction, canal: discord.TextChannel):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
    if not has_allowed_role(interaction):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return
    added = await add_ignored_channel(interaction.guild.id, canal.id)
    if added:
        await interaction.response.send_message(f"✅ {canal.mention} añadido a la lista de ignorados.", ephemeral=True)
    else:
        await interaction.response.send_message(f"ℹ️ {canal.mention} ya estaba en la lista de ignorados.", ephemeral=True)


@_corpus_ignorar.command(name="quitar", description="Quita un canal de la lista de ignorados.")
@app_commands.describe(canal="Canal que el bot debe dejar de ignorar")
async def corpus_ignorar_quitar(interaction: discord.Interaction, canal: discord.TextChannel):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
    if not has_allowed_role(interaction):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return
    removed = await remove_ignored_channel(interaction.guild.id, canal.id)
    if removed:
        await interaction.response.send_message(f"✅ {canal.mention} quitado de la lista de ignorados.", ephemeral=True)
    else:
        await interaction.response.send_message(f"ℹ️ {canal.mention} no estaba en la lista de ignorados.", ephemeral=True)


@_corpus_ignorar.command(name="lista", description="Muestra los canales que el bot ignora actualmente.")
async def corpus_ignorar_lista(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
    if not has_allowed_role(interaction):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return
    channel_ids = await list_ignored_channels(interaction.guild.id)
    if not channel_ids:
        await interaction.response.send_message("ℹ️ No hay canales ignorados.", ephemeral=True)
        return
    lines = [f"• <#{cid}>" for cid in channel_ids]
    await interaction.response.send_message(
        "**Canales ignorados:**\n" + "\n".join(lines),
        ephemeral=True,
    )


bot.tree.add_command(_corpus_ignorar)


_meme_auto = app_commands.Group(
    name="meme_auto",
    description="Gestiona los memes automáticos por canal",
)


@_meme_auto.command(name="activar", description="Activa memes automáticos en un canal.")
@app_commands.describe(
    canal="Canal donde se postarán los memes",
    intervalo="Intervalo en horas (mínimo 2, máximo 24)",
)
async def meme_auto_activar(interaction: discord.Interaction, canal: discord.TextChannel, intervalo: int):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
    if not has_allowed_role(interaction):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return
    if intervalo < 2:
        await interaction.response.send_message("el intervalo mínimo es 2 horas", ephemeral=True)
        return
    if intervalo > 24:
        await interaction.response.send_message("el intervalo máximo es 24 horas", ephemeral=True)
        return
    await add_meme_schedule(interaction.guild.id, canal.id, intervalo * 60)
    await interaction.response.send_message(
        f"✅ Memes automáticos activados en {canal.mention} cada {intervalo} horas.",
        ephemeral=True,
    )


@_meme_auto.command(name="desactivar", description="Desactiva los memes automáticos en un canal.")
@app_commands.describe(canal="Canal donde desactivar los memes automáticos")
async def meme_auto_desactivar(interaction: discord.Interaction, canal: discord.TextChannel):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
    if not has_allowed_role(interaction):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return
    removed = await remove_meme_schedule(interaction.guild.id, canal.id)
    if removed:
        await interaction.response.send_message(
            f"✅ Memes automáticos desactivados en {canal.mention}.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"ℹ️ ese canal no tenía memes automáticos",
            ephemeral=True,
        )


@_meme_auto.command(name="lista", description="Muestra los canales con memes automáticos configurados.")
async def meme_auto_lista(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
    if not has_allowed_role(interaction):
        await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
        return
    schedules = await list_meme_schedules(interaction.guild.id)
    if not schedules:
        await interaction.response.send_message("ℹ️ no hay canales configurados", ephemeral=True)
        return
    lines = []
    now = datetime.utcnow()
    for s in schedules:
        horas = s["interval_minutes"] // 60
        if s["last_posted_at"] is not None:
            try:
                last_dt = datetime.fromisoformat(s["last_posted_at"])
                delta_h = int((now - last_dt).total_seconds() // 3600)
                ultimo = f"hace {delta_h} horas"
            except Exception:
                ultimo = "desconocido"
        else:
            ultimo = "nunca"
        lines.append(f"• <#{s['channel_id']}> — cada {horas} horas — último: {ultimo}")
    await interaction.response.send_message(
        "**Memes automáticos:**\n" + "\n".join(lines),
        ephemeral=True,
    )


bot.tree.add_command(_meme_auto)


@bot.tree.command(name="momo", description="Genera un meme del server.")
async def momo_slash(interaction: discord.Interaction):
    import time
    now = time.time()
    last = _momo_cooldowns.get(interaction.user.id, 0)
    if now - last < 45:
        remaining = int(45 - (now - last))
        await interaction.response.send_message(
            f"espera {remaining}s antes de generar otro meme",
            ephemeral=True
        )
        return
    _momo_cooldowns[interaction.user.id] = now

    await interaction.response.defer()

    if not interaction.guild:
        await interaction.followup.send("Solo en servidores.", ephemeral=True)
        return

    try:
        guild_id = interaction.guild.id
        last_img = _last_meme_image.get(guild_id)
        image_url = await get_random_image_url_excluding(guild_id, exclude_url=last_img)
        if image_url:
            _last_meme_image[guild_id] = image_url
        if not image_url:
            await interaction.followup.send(
                "Sin imágenes en el pool todavía. Sube algunas fotos al server primero.",
                ephemeral=True,
            )
            return

        try:
            img_resp = await asyncio.to_thread(requests.get, image_url, timeout=15)
            if img_resp.status_code != 200:
                await interaction.followup.send("No se pudo descargar la imagen.", ephemeral=True)
                return
            img_bytes = img_resp.content
        except Exception:
            log.exception("momo: error descargando imagen %s", image_url)
            await interaction.followup.send("No se pudo descargar la imagen.", ephemeral=True)
            return

        if len(img_bytes) > _MEME_MAX_BYTES:
            await interaction.followup.send("La imagen pesa mucho.", ephemeral=True)
            return

        corpus_sample = await get_corpus_messages_filtered(guild_id, min_words=5, limit=400)
        if not corpus_sample:
            await interaction.followup.send("El corpus está vacío.", ephemeral=True)
            return

        caption = await generate_groq_meme_caption(img_bytes, corpus_sample)
        if caption is None:
            model = await build_markov_model(guild_id)
            caption = await asyncio.to_thread(
                _try_short_sentence, model
            ) if model and not model.is_empty else None

        if not caption:
            await interaction.followup.send("No se pudo generar el caption.", ephemeral=True)
            return

        meme_bytes = await asyncio.to_thread(render_meme, img_bytes, caption)
        await interaction.followup.send(
            file=discord.File(io.BytesIO(meme_bytes), filename="meme.png")
        )

    except Exception:
        log.exception("momo: error inesperado")
        await interaction.followup.send("se rompió algo, revisa los logs.", ephemeral=True)


if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except discord.errors.LoginFailure:
        log.critical("Token inválido. Verifica DISCORD_TOKEN en .env.")
        sys.exit(1)
