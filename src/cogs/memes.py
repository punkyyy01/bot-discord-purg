"""Memes: /momo, memes automáticos, captura de imágenes con 🎯 y captions Groq/Markov."""

import asyncio
import io
import logging
import os
import time
from datetime import datetime, timezone

import discord
import requests
from discord import app_commands
from discord.ext import commands, tasks
from groq import AsyncGroq

import r2
from cogs.premium import is_premium_guild
from config import BOT_TRIGGER_NAME, GROQ_API_KEY, GROQ_GUILD_COOLDOWN, MEME_MAX_BYTES
from db import (
    add_meme_schedule,
    delete_image_url,
    get_corpus_messages_filtered,
    get_due_meme_schedules,
    get_random_image_url_excluding,
    list_meme_schedules,
    remove_meme_schedule,
    save_image_url,
    update_meme_last_posted,
)
from generation import build_markov_model
from meme_generator import _try_short_sentence, render_caption
from utils import LRUDict, has_admin_permission

log = logging.getLogger(__name__)

_groq_client = AsyncGroq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

_momo_cooldowns: LRUDict = LRUDict(512)
_groq_cooldowns: LRUDict = LRUDict(256)
_last_meme_image: LRUDict = LRUDict(256)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def is_meme_trigger(bot: commands.Bot, message: discord.Message) -> bool:
    parts = (message.content or "").strip().lower().split()
    if parts == [BOT_TRIGGER_NAME, "generar"]:
        return True
    if bot.user:
        for mention in (f"<@{bot.user.id}>", f"<@!{bot.user.id}>"):
            if parts == [mention, "generar"]:
                return True
    return False


def _detect_image_mime(image_bytes: bytes) -> str:
    if image_bytes[:4] == b'\x89PNG':
        return "image/png"
    if image_bytes[:2] == b'\xff\xd8':
        return "image/jpeg"
    if image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return "image/webp"
    if image_bytes[:3] == b'GIF':
        return "image/gif"
    return "image/jpeg"


async def generate_groq_meme_caption(
    image_bytes: bytes,
    corpus_sample: list[str],
    guild_id: int = 0,
) -> str | None:
    if not _groq_client:
        return None

    now = time.monotonic()
    if guild_id and now - _groq_cooldowns.get(guild_id, 0.0) < GROQ_GUILD_COOLDOWN:
        log.debug("Groq cooldown activo para guild %s, fallback a Markov", guild_id)
        return None
    if guild_id:
        _groq_cooldowns[guild_id] = now

    import base64
    mime_type = _detect_image_mime(image_bytes)
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    short_msgs = [m for m in corpus_sample if len(m.split()) <= 5]
    long_msgs = [m for m in corpus_sample if len(m.split()) > 5]
    voice_sample = short_msgs[:25] + long_msgs[:15]
    corpus_text = "\n".join(voice_sample)

    system_prompt = (
        "You write ironic meme captions for a Spanish-speaking Discord server. "
        "The humor comes from dissonance: the caption introduces a subject, expectation, or context "
        "that clashes with what the image actually shows — that clash is the joke. "
        "Never describe or narrate the image. "
        "Techniques: attribute the image to a subject that clearly doesn't fit it; "
        "set up a high expectation that the image deflates; "
        "present the image as evidence of the opposite of what it shows; "
        "compare the image to a grand or grave concept it contradicts. "
        "POV and CUANDO only work when what follows contrasts with the image, not when it summarizes it. "
        "Use the vocabulary and tone from the server corpus. "
        "Max 80 characters. Return only the caption text, nothing else."
    )

    user_prompt = (
        f"Vocabulario y registro de este server:\n"
        f"{corpus_text}\n\n"
        f"Mira la imagen. Escribe un caption que cree contraste con lo que muestra: "
        f"introducí un sujeto, expectativa o contexto que no encaje. "
        f"Usa las palabras y el registro de arriba. "
        f"Máximo 80 caracteres."
    )

    try:
        response = await _groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{image_b64}"
                            },
                        },
                        {"type": "text", "text": user_prompt},
                    ],
                },
            ],
            max_tokens=120,
            temperature=0.9,
        )
        caption = response.choices[0].message.content.strip()
        caption = caption.strip('"').strip("'").strip()
        if len(caption) > 80:
            caption = caption[:80].rsplit(" ", 1)[0].strip()
        return caption if caption else None
    except Exception as e:
        if "429" in str(e) or "rate_limit" in str(e).lower():
            log.warning("Groq rate limit, fallback a Markov")
            return None
        log.exception("Error en Groq caption")
        return None


async def _pick_pool_image(guild_id: int, log_prefix: str) -> tuple[bytes | None, str | None]:
    """Elige una imagen válida del pool, con eviction lazy de URLs muertas/expiradas."""
    img_bytes = None
    image_url = None
    last = _last_meme_image.get(guild_id)
    for _ in range(10):
        url_candidate = await get_random_image_url_excluding(guild_id, exclude_url=last)
        if not url_candidate:
            break
        try:
            img_resp = await asyncio.to_thread(requests.get, url_candidate, timeout=15)
            if img_resp.status_code == 200 and len(img_resp.content) <= MEME_MAX_BYTES:
                img_bytes = img_resp.content
                image_url = url_candidate
                break
            else:
                log.warning("%s: URL inválida (HTTP %s), eliminando: %s", log_prefix, img_resp.status_code, url_candidate)
                await delete_image_url(guild_id, url_candidate)
        except Exception:
            log.warning("%s: error descargando, eliminando del pool: %s", log_prefix, url_candidate)
            await delete_image_url(guild_id, url_candidate)
    if image_url:
        _last_meme_image[guild_id] = image_url
    return img_bytes, image_url


async def _generate_caption(guild_id: int, img_bytes: bytes, corpus_sample: list[str]) -> str | None:
    caption = None
    if _groq_client:
        caption = await generate_groq_meme_caption(img_bytes, corpus_sample, guild_id=guild_id)
    if not caption:
        model = await build_markov_model(guild_id)
        if model and not model.is_empty:
            caption = await asyncio.to_thread(_try_short_sentence, model)
    return caption


async def handle_meme_command(message: discord.Message) -> None:
    if not is_premium_guild(message.guild.id if message.guild else None):
        return

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
        await message.reply("necesito que respondas a un mensaje que tenga una imagen")
        return

    log.info("handle_meme_command: imagen encontrada filename=%r size=%d bytes", image_att.filename, image_att.size)

    if image_att.size > MEME_MAX_BYTES:
        await message.reply("la imagen supera el límite de 10MB")
        return

    try:
        img_bytes = await image_att.read()
    except Exception:
        log.exception("Error descargando imagen para meme")
        await message.reply("ocurrió un error, intenta de nuevo")
        return

    if not message.guild:
        log.info("handle_meme_command: mensaje fuera de guild, no hay modelo Markov disponible")
        await message.reply("no pude generar el meme, intenta de nuevo")
        return

    log.info("handle_meme_command: generando caption")

    text = None
    if _groq_client:
        corpus_sample = await get_corpus_messages_filtered(message.guild.id, min_words=1, limit=400)
        if corpus_sample:
            text = await generate_groq_meme_caption(img_bytes, corpus_sample, guild_id=message.guild.id)
    if not text:
        model = await build_markov_model(message.guild.id)
        if model and not model.is_empty:
            text = await asyncio.to_thread(_try_short_sentence, model)
    log.info("handle_meme_command: texto generado=%r", text)
    if not text:
        await message.reply("no pude generar el meme, intenta de nuevo")
        return

    try:
        meme_bytes = await asyncio.to_thread(render_caption, img_bytes, text)
    except Exception:
        log.exception("Error generando meme con Pillow")
        await message.reply("ocurrió un error, intenta de nuevo")
        return

    await message.reply(file=discord.File(io.BytesIO(meme_bytes), filename="meme.png"))


class Memes(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        if not _groq_client:
            log.info("GROQ_API_KEY no configurada: captions de memes usarán solo Markov.")
        self.auto_meme_task.start()

    async def cog_unload(self) -> None:
        self.auto_meme_task.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if is_meme_trigger(self.bot, message):
            await handle_meme_command(message)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) != "🎯":
            return
        if payload.guild_id is None:
            return
        if not is_premium_guild(payload.guild_id):
            return
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except Exception:
            return
        if message.author.bot:
            return
        added = False
        for attachment in message.attachments:
            ext = os.path.splitext(attachment.filename.lower())[1]
            if ext in {".png", ".jpg", ".jpeg", ".webp"} \
                    and attachment.size <= MEME_MAX_BYTES:
                try:
                    if r2.available():
                        final_url = await asyncio.to_thread(
                            r2.upload_image_sync, attachment.url, payload.guild_id, ext
                        )
                        if not final_url:
                            log.warning("No se pudo subir imagen a R2, usando URL original: %s", attachment.url)
                            final_url = attachment.url
                    else:
                        final_url = attachment.url
                    inserted = await save_image_url(payload.guild_id, final_url)
                    if inserted:
                        added = True
                        log.info("Imagen agregada al pool 🎯: %s", final_url)
                except Exception:
                    log.exception("Error guardando imagen por reaccion")
        if added:
            try:
                await message.add_reaction("✅")
            except Exception:
                pass

    @tasks.loop(minutes=10)
    async def auto_meme_task(self):
        schedules = await get_due_meme_schedules()
        for schedule in schedules:
            try:
                channel = self.bot.get_channel(schedule["channel_id"])
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue

                guild_id = schedule["guild_id"]
                if not is_premium_guild(guild_id):
                    continue

                img_bytes, image_url = await _pick_pool_image(guild_id, "auto_meme")
                if not image_url:
                    log.info("auto_meme: sin imágenes válidas en pool para guild %s", guild_id)
                    continue

                corpus_sample = await get_corpus_messages_filtered(guild_id, min_words=1, limit=400)
                if not corpus_sample:
                    log.info("auto_meme: corpus vacío para guild %s", guild_id)
                    continue

                caption = await _generate_caption(guild_id, img_bytes, corpus_sample)
                if not caption:
                    log.info("auto_meme: no se generó caption para guild %s", guild_id)
                    continue

                meme_bytes = await asyncio.to_thread(render_caption, img_bytes, caption)
                await channel.send(
                    file=discord.File(io.BytesIO(meme_bytes), filename="meme.png")
                )
                await update_meme_last_posted(guild_id, schedule["channel_id"])
                log.info(
                    "auto_meme: meme posteado en canal %s con caption: %s",
                    schedule["channel_id"], caption
                )

            except Exception:
                log.exception(
                    "auto_meme: error inesperado para canal %s",
                    schedule["channel_id"]
                )

    @auto_meme_task.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def _momo_impl(self, interaction: discord.Interaction) -> None:
        if not is_premium_guild(interaction.guild_id):
            await interaction.response.send_message("esta función no está disponible en este servidor", ephemeral=True)
            return
        now = time.time()
        cooldown_key = (interaction.guild_id or 0, interaction.user.id)
        last = _momo_cooldowns.get(cooldown_key, 0)
        if now - last < 45:
            remaining = int(45 - (now - last))
            await interaction.response.send_message(
                f"espera {remaining} segundos antes de generar otro meme",
                ephemeral=True
            )
            return
        _momo_cooldowns[cooldown_key] = now

        await interaction.response.defer()

        if not interaction.guild:
            await interaction.followup.send("Solo en servidores.", ephemeral=True)
            return

        try:
            guild_id = interaction.guild.id

            img_bytes, image_url = await _pick_pool_image(guild_id, "momo")
            if not image_url:
                await interaction.followup.send(
                    "Sin imágenes válidas en el pool. Añade fotos con 🎯.",
                    ephemeral=True,
                )
                return

            corpus_sample = await get_corpus_messages_filtered(guild_id, min_words=1, limit=400)
            if not corpus_sample:
                await interaction.followup.send("El corpus está vacío.", ephemeral=True)
                return

            caption = await _generate_caption(guild_id, img_bytes, corpus_sample)
            if not caption:
                await interaction.followup.send("No se pudo generar el caption.", ephemeral=True)
                return

            meme_bytes = await asyncio.to_thread(render_caption, img_bytes, caption)
            await interaction.followup.send(
                file=discord.File(io.BytesIO(meme_bytes), filename="meme.png")
            )

        except Exception:
            log.exception("momo: error inesperado")
            await interaction.followup.send("se rompió algo, revisa los logs.", ephemeral=True)

    @app_commands.command(name="momo", description="Genera un meme del server.")
    async def momo(self, interaction: discord.Interaction):
        await self._momo_impl(interaction)

    @app_commands.command(name="meme", description="Genera un meme del server.")
    async def meme(self, interaction: discord.Interaction):
        await self._momo_impl(interaction)

    meme_auto = app_commands.Group(
        name="meme_auto",
        description="Gestiona los memes automáticos por canal",
    )

    @meme_auto.command(name="activar", description="Activa memes automáticos en un canal.")
    @app_commands.describe(
        canal="Canal donde se postarán los memes",
        intervalo="Intervalo en horas (mínimo 2, máximo 24)",
    )
    async def meme_auto_activar(self, interaction: discord.Interaction, canal: discord.TextChannel, intervalo: int):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
            return
        if not is_premium_guild(interaction.guild_id):
            await interaction.response.send_message("esta función no está disponible en este servidor", ephemeral=True)
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

    @meme_auto.command(name="desactivar", description="Desactiva los memes automáticos en un canal.")
    @app_commands.describe(canal="Canal donde desactivar los memes automáticos")
    async def meme_auto_desactivar(self, interaction: discord.Interaction, canal: discord.TextChannel):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
            return
        if not is_premium_guild(interaction.guild_id):
            await interaction.response.send_message("esta función no está disponible en este servidor", ephemeral=True)
            return
        removed = await remove_meme_schedule(interaction.guild.id, canal.id)
        if removed:
            await interaction.response.send_message(
                f"✅ Memes automáticos desactivados en {canal.mention}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "ℹ️ ese canal no tenía memes automáticos",
                ephemeral=True,
            )

    @meme_auto.command(name="lista", description="Muestra los canales con memes automáticos configurados.")
    async def meme_auto_lista(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
            return
        if not is_premium_guild(interaction.guild_id):
            await interaction.response.send_message("esta función no está disponible en este servidor", ephemeral=True)
            return
        schedules = await list_meme_schedules(interaction.guild.id)
        if not schedules:
            await interaction.response.send_message("ℹ️ no hay canales configurados", ephemeral=True)
            return
        lines = []
        now = datetime.now(timezone.utc).replace(tzinfo=None)
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Memes(bot))
