"""Notificaciones de YouTube: suscripciones por RSS y chequeo periódico."""

import asyncio
import logging

import discord
import feedparser
from discord import app_commands
from discord.ext import commands, tasks

from db import (
    add_youtube_sub,
    get_all_youtube_subs,
    list_youtube_subs,
    remove_youtube_sub,
    set_youtube_mention_role,
    update_last_video_id,
)
from utils import has_admin_permission

log = logging.getLogger(__name__)


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


class YouTube(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.check_youtube.start()

    async def cog_unload(self) -> None:
        self.check_youtube.cancel()

    @tasks.loop(minutes=15)
    async def check_youtube(self):
        subs = await get_all_youtube_subs()

        async def _check_one(sub: dict) -> None:
            try:
                video = await get_latest_video(sub["youtube_channel_id"])
                if video is None:
                    return
                if video["id"] != sub["last_video_id"]:
                    channel = self.bot.get_channel(sub["discord_channel_id"])
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

    @check_youtube.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="youtube_add", description="Suscribe un canal de YouTube para notificaciones en un canal de Discord.")
    @app_commands.describe(
        youtube_channel_id="ID del canal de YouTube (empieza con UC...)",
        discord_channel="Canal de Discord donde se avisarán los nuevos videos",
        rol="Rol a mencionar cuando haya un video nuevo (opcional)",
    )
    async def youtube_add(
        self,
        interaction: discord.Interaction,
        youtube_channel_id: str,
        discord_channel: discord.TextChannel,
        rol: discord.Role | None = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
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

    @app_commands.command(name="youtube_remove", description="Elimina la suscripción a un canal de YouTube.")
    @app_commands.describe(youtube_channel_id="ID del canal de YouTube a eliminar")
    async def youtube_remove(self, interaction: discord.Interaction, youtube_channel_id: str):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
            return

        removed = await remove_youtube_sub(interaction.guild.id, youtube_channel_id)
        if removed:
            await interaction.response.send_message(f"✅ Suscripción a `{youtube_channel_id}` eliminada.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ No había suscripción activa para `{youtube_channel_id}`.", ephemeral=True)

    @app_commands.command(name="youtube_list", description="Muestra todas las suscripciones de YouTube activas del servidor.")
    async def youtube_list(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
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

    @app_commands.command(name="youtube_set_mention", description="Configura el rol a mencionar en las notificaciones de un canal de YouTube.")
    @app_commands.describe(
        channel_id="ID del canal de YouTube",
        rol="Rol a mencionar (omitir para quitar la mención)",
    )
    async def youtube_set_mention(
        self,
        interaction: discord.Interaction,
        channel_id: str,
        rol: discord.Role | None = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(YouTube(bot))
