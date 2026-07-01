"""Sistema premium_guilds: estado compartido + comandos de gestión (solo owner)."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import BOT_OWNER_ID, PURGATORY_GUILD_ID
from db import add_premium_guild, list_premium_guilds, remove_premium_guild

log = logging.getLogger(__name__)

# Set poblado desde la tabla premium_guilds al cargar el cog; consultado por
# el resto de cogs en cada feature premium.
_premium_guild_ids: set[int] = set()


def is_premium_guild(guild_id: int | None) -> bool:
    """Retorna True si el guild tiene acceso a features premium (memes, pool de imágenes, etc.)."""
    if guild_id == PURGATORY_GUILD_ID:
        return True
    if guild_id is None:
        return False
    return guild_id in _premium_guild_ids


def discard_premium_guild(guild_id: int) -> None:
    _premium_guild_ids.discard(guild_id)


def _is_owner(interaction: discord.Interaction) -> bool:
    return bool(BOT_OWNER_ID and interaction.user.id == BOT_OWNER_ID)


class Premium(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        global _premium_guild_ids
        _premium_guild_ids = {g["guild_id"] for g in await list_premium_guilds()}
        log.info("Servidores premium cargados: %s", _premium_guild_ids)

    premium = app_commands.Group(
        name="premium",
        description="Gestiona servidores premium (solo bot owner)",
        guild_only=False,
    )

    @premium.command(name="add", description="Agrega un servidor al plan premium.")
    @app_commands.describe(guild_id="ID del servidor", nota="Nota opcional")
    async def premium_add(self, interaction: discord.Interaction, guild_id: str, nota: str | None = None):
        if not _is_owner(interaction):
            await interaction.response.send_message("no tenés permiso", ephemeral=True)
            return
        try:
            gid = int(guild_id)
        except ValueError:
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        added = await add_premium_guild(gid, nota)
        if added:
            _premium_guild_ids.add(gid)
            guild_obj = self.bot.get_guild(gid)
            name = guild_obj.name if guild_obj else str(gid)
            await interaction.response.send_message(f"✅ `{name}` ({gid}) agregado como premium.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ El servidor `{gid}` ya era premium.", ephemeral=True)

    @premium.command(name="quitar", description="Quita un servidor del plan premium.")
    @app_commands.describe(guild_id="ID del servidor")
    async def premium_quitar(self, interaction: discord.Interaction, guild_id: str):
        if not _is_owner(interaction):
            await interaction.response.send_message("no tenés permiso", ephemeral=True)
            return
        try:
            gid = int(guild_id)
        except ValueError:
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        removed = await remove_premium_guild(gid)
        if removed:
            _premium_guild_ids.discard(gid)
            await interaction.response.send_message(f"✅ Servidor `{gid}` quitado del plan premium.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ El servidor `{gid}` no estaba en premium.", ephemeral=True)

    @premium.command(name="lista", description="Lista los servidores premium.")
    async def premium_lista(self, interaction: discord.Interaction):
        if not _is_owner(interaction):
            await interaction.response.send_message("no tenés permiso", ephemeral=True)
            return
        guilds_list = await list_premium_guilds()
        if not guilds_list:
            await interaction.response.send_message("ℹ️ No hay servidores premium registrados.", ephemeral=True)
            return
        lines = []
        for g in guilds_list:
            guild_obj = self.bot.get_guild(g["guild_id"])
            name = guild_obj.name if guild_obj else "—"
            note = f" — {g['note']}" if g["note"] else ""
            lines.append(f"• `{g['guild_id']}` {name} (desde {g['added_at'][:10]}){note}")
        body = "**Servidores premium:**\n" + "\n".join(lines)
        if len(body) > 1900:
            body = body[:1900] + "\n…"
        await interaction.response.send_message(body, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Premium(bot))
