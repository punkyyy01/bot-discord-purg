"""Chat: corpus, Markov, respuestas automáticas, frases especiales y reacciones."""

import logging
import random

import discord
from discord import app_commands
from discord.ext import commands

import generation
from cogs.gifs import save_gif_candidates
from cogs.memes import is_meme_trigger
from config import REFEED_ALL_MAX_MESSAGES, REFEED_MAX_MESSAGES
from db import (
    add_frase_especial,
    add_ignored_channel,
    add_reaction_to_pool,
    count_corpus_messages,
    count_user_messages,
    delete_frase_especial,
    get_chat_settings,
    get_frase_especial,
    get_random_gif,
    get_random_reaction,
    is_channel_ignored,
    list_frases_especiales,
    list_ignored_channels,
    list_reaction_pool,
    remove_ignored_channel,
    remove_reaction_from_pool,
    save_corpus_and_user_message,
    set_chat_mode,
    wipe_corpus,
)
from utils import chunk_message, has_admin_permission

log = logging.getLogger(__name__)


class Chat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _save_message_to_corpus(self, guild_id: int, message: discord.Message) -> bool:
        """Limpia y guarda un mensaje en corpus + user_corpus. Retorna si se insertó al corpus."""
        cleaned = generation.clean_for_corpus(message.content or "")
        if cleaned is None:
            return False
        corpus_ins, user_ins = await save_corpus_and_user_message(
            guild_id, message.channel.id,
            message.author.id, message.author.display_name, cleaned,
            message_id=message.id,
        )
        if corpus_ins:
            generation.note_corpus_insert(guild_id, message.channel.id)
        if user_ins:
            generation.note_user_corpus_insert(guild_id, message.author.id)
        return corpus_ins

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if is_meme_trigger(self.bot, message):
            return  # lo maneja el cog de memes; no entra al corpus
        if (message.content or "").strip().startswith("!"):
            return  # comandos de prefijo: los procesa commands.Bot

        auto_generate = False

        if message.guild:
            if await is_channel_ignored(message.guild.id, message.channel.id):
                return

            inserted = await self._save_message_to_corpus(message.guild.id, message)
            if inserted:
                auto_generate = generation.note_message_for_auto_generate(
                    message.guild.id, message.channel.id
                )

            # Reacción aleatoria con emoji del pool configurable
            if random.random() < 0.05:
                try:
                    reaction = await get_random_reaction(message.guild.id)
                    if reaction:
                        await message.add_reaction(reaction["emoji_text"])
                except Exception:
                    log.exception("Error añadiendo reacción emoji")

        # Verificar si el bot fue mencionado o si le respondieron a él directamente
        mention_bot = bool(self.bot.user and self.bot.user.id in (message.raw_mentions or []))
        reply_to_bot = False
        if message.reference and message.reference.message_id and self.bot.user:
            ref_msg = message.reference.resolved
            if isinstance(ref_msg, discord.Message):
                reply_to_bot = ref_msg.author.id == self.bot.user.id

        if not (mention_bot or reply_to_bot):
            if message.guild and auto_generate:
                try:
                    if random.random() < 0.45:
                        gif_url = await get_random_gif(message.guild.id)
                        if gif_url:
                            await message.channel.send(gif_url)
                            return
                    text, is_special = await generation.generate_response(message.guild.id)
                    if text is not None:
                        final = text if is_special else generation.post_process_reply(text)
                        for chunk in chunk_message(final):
                            await message.channel.send(chunk)
                except Exception:
                    log.exception("Error en generación automática de respuesta")
            return

        if not message.guild:
            return

        # Respetar restricciones de canal y modo de chat
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

        text, is_special = await generation.generate_response(message.guild.id)
        if text is None:
            reply = "..."
        elif is_special:
            reply = text
        else:
            reply = generation.post_process_reply(text)
        for chunk in chunk_message(reply):
            await message.reply(chunk)

    # --- COMANDOS ---

    @app_commands.command(name="generar", description="Genera un mensaje usando el modelo Markov del canal.")
    async def generar(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        if interaction.channel is None:
            await interaction.followup.send("No puedo determinar el canal.", ephemeral=True)
            return
        text, is_special = await generation.generate_response(interaction.guild.id)
        if text is None:
            reply = "..."
        elif is_special:
            reply = text
        else:
            reply = generation.post_process_reply(text)
        await interaction.followup.send(reply)

    @app_commands.command(name="imitar", description="Genera un mensaje imitando el estilo de un usuario del servidor.")
    @app_commands.describe(usuario="Usuario a imitar")
    async def imitar(self, interaction: discord.Interaction, usuario: discord.Member):
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

        result = await generation.generate_markov_for_user(interaction.guild.id, usuario.id)
        if result is None:
            await interaction.followup.send(
                f"⚠️ No se pudo generar un mensaje para **{usuario.display_name}**. Intenta más tarde."
            )
            return

        await interaction.followup.send(f'🎭 **{usuario.display_name}** diría: "{result}"')

    @app_commands.command(name="chatmode", description="Activa o desactiva las respuestas automáticas del bot al mencionarlo.")
    @app_commands.describe(
        estado="Activar o desactivar",
        canal="Canal específico para auto-reply (opcional, por defecto todos)"
    )
    @app_commands.choices(estado=[
        app_commands.Choice(name="Activar", value="on"),
        app_commands.Choice(name="Desactivar", value="off"),
    ])
    async def chatmode(self, interaction: discord.Interaction, estado: app_commands.Choice[str], canal: discord.TextChannel | None = None):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
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

    # --- CORPUS ---

    @app_commands.command(name="refeed", description="Guarda los últimos mensajes del canal en el corpus del modelo Markov.")
    async def refeed(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return

        if not has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        channel = interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            await interaction.followup.send("No puedo leer el historial de este canal.")
            return

        if await is_channel_ignored(interaction.guild.id, channel.id):
            await interaction.followup.send("⚠️ Este canal está en la lista de ignorados. Usa `/corpus_ignorar quitar` primero si quieres incluirlo.")
            return

        saved = 0
        fetched = 0

        last_msg_id: int | None = None
        while fetched < REFEED_MAX_MESSAGES:
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
                await save_gif_candidates(interaction.guild.id, msg)
                if await self._save_message_to_corpus(interaction.guild.id, msg):
                    saved += 1

            last_msg_id = batch[-1].id

        result = f"✅ Guardados {saved} mensajes en el corpus."
        if fetched >= REFEED_MAX_MESSAGES:
            result += f"\n⚠️ Límite de {REFEED_MAX_MESSAGES:,} mensajes leídos alcanzado; el canal puede tener más."
        await interaction.followup.send(result)

    @app_commands.command(name="refeed_all", description="Guarda mensajes de todos los canales de texto del servidor en el corpus del modelo Markov.")
    async def refeed_all(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return

        if not has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        me = interaction.guild.me
        if me is None and self.bot.user is not None:
            me = interaction.guild.get_member(self.bot.user.id)
        if me is None:
            await interaction.followup.send("No puedo determinar los permisos del bot.")
            return

        total_saved = 0
        any_channel_hit_limit = False

        for channel in interaction.guild.text_channels:
            perms = channel.permissions_for(me)
            if not (perms.read_messages and perms.read_message_history):
                continue
            if await is_channel_ignored(interaction.guild.id, channel.id):
                continue

            channel_fetched = 0
            last_msg_id: int | None = None
            while channel_fetched < REFEED_ALL_MAX_MESSAGES:
                before_obj = discord.Object(id=last_msg_id) if last_msg_id else None
                try:
                    batch = [msg async for msg in channel.history(limit=100, before=before_obj, oldest_first=False)]
                except discord.Forbidden:
                    break
                if not batch:
                    break
                channel_fetched += len(batch)

                for msg in batch:
                    if msg.author.bot:
                        continue
                    await save_gif_candidates(interaction.guild.id, msg)
                    if await self._save_message_to_corpus(interaction.guild.id, msg):
                        total_saved += 1

                last_msg_id = batch[-1].id

            if channel_fetched >= REFEED_ALL_MAX_MESSAGES:
                any_channel_hit_limit = True

        result = f"✅ Refeed_all completado. Total guardado: {total_saved} mensajes."
        if any_channel_hit_limit:
            result += f"\n⚠️ Límite de {REFEED_ALL_MAX_MESSAGES:,} mensajes leídos alcanzado; algunos canales pueden estar incompletos."
        await interaction.followup.send(result)

    @app_commands.command(name="corpus_wipe", description="Borra el corpus del servidor (mensajes) y reinicia el cache Markov.")
    async def corpus_wipe(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            await interaction.followup.send("Solo en servidores.", ephemeral=True)
            return

        if not has_admin_permission(interaction):
            await interaction.followup.send("❌ No tienes permisos para usar este comando.", ephemeral=True)
            return

        await wipe_corpus(interaction.guild.id)
        generation.reset_guild_caches(interaction.guild.id)

        await interaction.followup.send("🗑️ Corpus limpiado. Corre /refeed_all para repoblarlo.")

    @app_commands.command(name="corpus_info", description="Muestra cuántos mensajes hay en el corpus del canal actual.")
    async def corpus_info(self, interaction: discord.Interaction):
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

    corpus_ignorar = app_commands.Group(
        name="corpus_ignorar",
        description="Gestiona los canales que el bot ignora completamente",
    )

    @corpus_ignorar.command(name="add", description="Añade un canal a la lista de ignorados.")
    @app_commands.describe(canal="Canal que el bot debe ignorar")
    async def corpus_ignorar_add(self, interaction: discord.Interaction, canal: discord.TextChannel):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
            return
        added = await add_ignored_channel(interaction.guild.id, canal.id)
        if added:
            await interaction.response.send_message(f"✅ {canal.mention} añadido a la lista de ignorados.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ {canal.mention} ya estaba en la lista de ignorados.", ephemeral=True)

    @corpus_ignorar.command(name="quitar", description="Quita un canal de la lista de ignorados.")
    @app_commands.describe(canal="Canal que el bot debe dejar de ignorar")
    async def corpus_ignorar_quitar(self, interaction: discord.Interaction, canal: discord.TextChannel):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
            return
        removed = await remove_ignored_channel(interaction.guild.id, canal.id)
        if removed:
            await interaction.response.send_message(f"✅ {canal.mention} quitado de la lista de ignorados.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ {canal.mention} no estaba en la lista de ignorados.", ephemeral=True)

    @corpus_ignorar.command(name="lista", description="Muestra los canales que el bot ignora actualmente.")
    async def corpus_ignorar_lista(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
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

    # --- FRASES ESPECIALES ---

    @app_commands.command(name="añadir_frase", description="Añade una frase especial al pool del servidor.")
    @app_commands.describe(frase="Frase que el bot puede soltar en cualquier momento")
    async def añadir_frase(self, interaction: discord.Interaction, frase: str):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        texto = frase.strip()
        if not texto:
            await interaction.response.send_message("❌ La frase no puede estar vacía.", ephemeral=True)
            return
        await add_frase_especial(interaction.guild.id, interaction.user.id, interaction.user.display_name, texto)
        await interaction.response.send_message("✅ Frase guardada.")

    @app_commands.command(name="ver_frases", description="Lista todas las frases especiales del servidor.")
    async def ver_frases(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        frases = await list_frases_especiales(interaction.guild.id)
        if not frases:
            await interaction.followup.send("ℹ️ No hay frases especiales en este servidor.")
            return
        lines = [
            f"`{f['id']}` — \"{f['frase']}\" — {f['user_name']} ({f['created_at'][:10]})"
            for f in frases
        ]
        body = "**Frases especiales:**\n" + "\n".join(lines)
        if len(body) > 1900:
            body = body[:1900] + "\n…(lista truncada)"
        await interaction.followup.send(body)

    @app_commands.command(name="borrar_frase", description="Borra una frase especial por su ID.")
    @app_commands.describe(id="ID de la frase a borrar (visible en /ver_frases)")
    async def borrar_frase(self, interaction: discord.Interaction, id: int):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        frase = await get_frase_especial(interaction.guild.id, id)
        if frase is None:
            await interaction.response.send_message("❌ No existe una frase con ese ID en este servidor.", ephemeral=True)
            return
        is_admin = (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.administrator
        )
        if frase["user_id"] != interaction.user.id and not is_admin:
            await interaction.response.send_message("❌ Solo puedes borrar tus propias frases.", ephemeral=True)
            return
        await delete_frase_especial(interaction.guild.id, id)
        await interaction.response.send_message("✅ Frase borrada.", ephemeral=True)

    # --- REACCIONES ---

    reacciones = app_commands.Group(
        name="reacciones",
        description="Gestiona el pool de emojis para las reacciones automáticas",
    )

    @reacciones.command(name="add", description="Añade un emoji al pool de reacciones automáticas.")
    @app_commands.describe(emoji="Emoji a añadir (Unicode 🔥 o custom del servidor <:nombre:id>)")
    async def reacciones_add(self, interaction: discord.Interaction, emoji: str):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
            return
        text = emoji.strip()
        if not text:
            await interaction.response.send_message("❌ El emoji no puede estar vacío.", ephemeral=True)
            return
        inserted = await add_reaction_to_pool(interaction.guild.id, text)
        if inserted:
            await interaction.response.send_message(f"✅ Emoji `{text}` añadido al pool.", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Ese emoji ya estaba en el pool.", ephemeral=True)

    @reacciones.command(name="quitar", description="Quita un emoji del pool por su ID (visible en /reacciones lista).")
    @app_commands.describe(id="ID del emoji a quitar")
    async def reacciones_quitar(self, interaction: discord.Interaction, id: int):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
            return
        removed = await remove_reaction_from_pool(interaction.guild.id, id)
        if removed:
            await interaction.response.send_message(f"✅ Emoji con ID `{id}` eliminado del pool.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ No existe un emoji con ID `{id}` en el pool.", ephemeral=True)

    @reacciones.command(name="lista", description="Muestra todos los emojis en el pool de reacciones.")
    async def reacciones_lista(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        if not has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permisos para usar este comando.", ephemeral=True)
            return
        pool = await list_reaction_pool(interaction.guild.id)
        if not pool:
            await interaction.response.send_message("ℹ️ El pool de reacciones está vacío. Usa `/reacciones add` para añadir emojis.", ephemeral=True)
            return
        lines = [f"`{r['id']}` — {r['emoji_text']}" for r in pool]
        body = "**Pool de reacciones:**\n" + "\n".join(lines)
        if len(body) > 1900:
            body = body[:1900] + "\n…(lista truncada)"
        await interaction.response.send_message(body, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Chat(bot))
