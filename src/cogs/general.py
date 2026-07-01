"""General: /help, !ping, manejo de errores y ciclo de vida de guilds (limpieza diferida)."""

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

import r2
from cogs.premium import discard_premium_guild
from config import PURGATORY_GUILD_ID, env_int
from db import (
    clear_guild_departure,
    get_expired_departures,
    list_gif_urls,
    list_image_urls,
    mark_guild_departed,
    purge_guild_data,
)
from help_view import HelpView, build_intro_embed

log = logging.getLogger(__name__)


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.guild_cleanup_task.start()

    async def cog_unload(self) -> None:
        self.guild_cleanup_task.cancel()

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context):
        await ctx.send("Pong!")

    @app_commands.command(name="help", description="Muestra los comandos de Purgito y cómo usarlos.")
    async def help(self, interaction: discord.Interaction):
        guild_name = interaction.guild.name if interaction.guild else "este servidor"
        embed = build_intro_embed(guild_name)
        view = HelpView(author_id=interaction.user.id, guild_name=guild_name)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ No tienes permisos para usar este comando.")
            return
        elif isinstance(error, commands.CommandNotFound):
            return
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("⚠️ Faltan argumentos. Revisa cómo usar el comando.")
            return
        log.error("Error en comando %s", getattr(ctx, "command", None), exc_info=error)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await clear_guild_departure(guild.id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        if guild.id == PURGATORY_GUILD_ID:
            return
        await mark_guild_departed(guild.id)
        log.info("on_guild_remove: guild %s (%s) marcado para limpieza diferida", guild.id, guild.name)

    @tasks.loop(hours=24)
    async def guild_cleanup_task(self):
        retention = env_int("GUILD_DATA_RETENTION_DAYS", 30)
        expired = await get_expired_departures(retention)
        if not expired:
            return
        purged = 0
        for guild_id in expired:
            try:
                if r2.available() and r2.public_url():
                    for item in await list_gif_urls(guild_id):
                        await r2.delete_url(item["url"])
                    for img_url in await list_image_urls(guild_id):
                        await r2.delete_url(img_url)
                await purge_guild_data(guild_id)
                discard_premium_guild(guild_id)
                purged += 1
            except Exception:
                log.exception("guild_cleanup: error purgando guild %s", guild_id)
        if purged:
            log.info("guild_cleanup: %d servidor(es) purgados", purged)

    @guild_cleanup_task.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
