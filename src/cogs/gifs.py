"""Captura y gestión de GIFs: detección en mensajes, subida a R2, galería web."""

import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands, tasks

import r2
import webapi
from config import PURGATORY_GUILD_ID
from db import count_gif_urls, get_unresolved_gifs, is_channel_ignored, save_gif_url, update_gif_media_url
from utils import has_admin_permission

log = logging.getLogger(__name__)

GIF_RE = re.compile(r'https?://\S*(tenor\.com|giphy\.com|cdn\.discordapp\.com/attachments/\S*\.gif)\S*', re.IGNORECASE)


async def save_gif_candidates(guild_id: int, message: discord.Message) -> None:
    """Guarda en la colección los GIFs (tenor/giphy/cdn) del contenido y adjuntos de un mensaje."""
    if message.content:
        for m in GIF_RE.finditer(message.content):
            try:
                url = m.group(0)
                if "cdn.discordapp.com" in url:
                    r2_url = await asyncio.to_thread(r2.upload_gif_sync, url, guild_id)
                    if r2_url == r2.GIF_TOO_LARGE:
                        continue
                    if r2_url:
                        url = r2_url
                await save_gif_url(guild_id, url)
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
                    r2_url = await asyncio.to_thread(r2.upload_gif_sync, url, guild_id)
                    if r2_url == r2.GIF_TOO_LARGE:
                        continue
                    if r2_url:
                        url = r2_url
                await save_gif_url(guild_id, url)
            except Exception:
                log.exception("Error guardando GIF adjunto: %s", attachment.url)


async def resolve_media_url(url: str) -> str | None:
    import requests
    try:
        if "cdn.discordapp.com" in url or (r2.public_url() and url.startswith(r2.public_url())):
            return url
        if "tenor.com" in url:
            resp = await asyncio.to_thread(
                requests.get, f"https://tenor.com/oembed?url={url}&format=json", timeout=8
            )
            return resp.json()["url"]
        if "giphy.com" in url:
            resp = await asyncio.to_thread(
                requests.get, f"https://giphy.com/services/oembed?url={url}&format=json", timeout=8
            )
            return resp.json()["thumbnail_url"]
    except Exception:
        return None
    return None


class Gifs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.resolve_gifs_task.start()
        try:
            await webapi.start_web_server()
        except Exception:
            log.exception("Error iniciando el servidor web")

    async def cog_unload(self) -> None:
        self.resolve_gifs_task.cancel()
        await webapi.stop_web_server()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if (message.content or "").strip().startswith("!"):
            return
        from cogs.memes import is_meme_trigger
        if is_meme_trigger(self.bot, message):
            return
        if await is_channel_ignored(message.guild.id, message.channel.id):
            return
        await save_gif_candidates(message.guild.id, message)

    @tasks.loop(seconds=90)
    async def resolve_gifs_task(self):
        gifs = await get_unresolved_gifs(PURGATORY_GUILD_ID, limit=25)
        if not gifs:
            return
        for gif in gifs:
            resolved = await resolve_media_url(gif["url"])
            if resolved is not None:
                await update_gif_media_url(gif["id"], resolved)
            await asyncio.sleep(1.5)

    @resolve_gifs_task.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="gif_add", description="Agrega un GIF a la colección del servidor.")
    @app_commands.describe(url="URL del GIF (tenor.com, giphy.com o cdn.discordapp.com)")
    async def gif_add(self, interaction: discord.Interaction, url: str):
        from cogs.premium import is_premium_guild
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
            return
        if not is_premium_guild(interaction.guild_id):
            await interaction.response.send_message("esta función no está disponible en este servidor", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        url = url.strip()
        if "cdn.discordapp.com" in url:
            final_url = await asyncio.to_thread(r2.upload_gif_sync, url, interaction.guild.id)
            if final_url == r2.GIF_TOO_LARGE:
                await interaction.followup.send("❌ El GIF supera el límite de tamaño permitido.")
                return
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Gifs(bot))
