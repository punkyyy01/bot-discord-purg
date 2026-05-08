import os
import sys
import re
import random
import markovify

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from db import (
    init_db,
    close_db,
    set_chat_mode,
    get_chat_settings,
    save_corpus_message,
    get_corpus_messages,
    count_corpus_messages,
    wipe_corpus,
    save_gif_url,
    get_random_gif,
)

# Cargar variables de entorno
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
ENABLE_MESSAGE_CONTENT = os.getenv("ENABLE_MESSAGE_CONTENT", "true").strip().lower() in ("1", "true", "yes")
GUILD_ID_ENV = os.getenv("GUILD_ID")

if not TOKEN:
    print("[ERROR] Falta DISCORD_TOKEN en .env. Copia .env.example a .env y pon tu token.")
    sys.exit(1)

# Configurar intents
intents = discord.Intents.default()
intents.message_content = ENABLE_MESSAGE_CONTENT

# Define `_CONECTORES_FINALES` at the top of the file to avoid undefined variable errors.
_CONECTORES_FINALES = [
    " y", " o", " con", " pero", " de", " para", " a", " que", " entonces", " como"
]

_markov_cache: dict[tuple[int, int], markovify.Text] = {}
_message_counter: dict[tuple[int, int], int] = {}
_corpus_insert_counter: dict[tuple[int, int], int] = {}

_GIF_RE = re.compile(r'https?://\S*(tenor\.com|giphy\.com|cdn\.discordapp\.com/attachments/\S*\.gif)\S*', re.IGNORECASE)

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
        _markov_cache.pop(key, None)
    else:
        _corpus_insert_counter[key] = n

# 1. BOT CUSTOM PARA CIERRE LIMPIO DE BASE DE DATOS
class MyCustomBot(commands.Bot):
    async def close(self):
        print("[INFO] Cerrando conexión a la base de datos...")
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

# Regex para eliminar emojis Unicode de la respuesta
_EMOJI_RE = re.compile(
    "[\U0001F600-\U0001F64F"  # emoticones
    "\U0001F300-\U0001F5FF"   # símbolos y pictogramas
    "\U0001F680-\U0001F6FF"   # transporte y mapas
    "\U0001F1E0-\U0001F1FF"   # banderas
    "\U00002702-\U000027B0"   # dingbats
    "\U0000FE00-\U0000FE0F"   # variaciones
    "\U0001F900-\U0001F9FF"   # suplementarios
    "\U0001FA00-\U0001FA6F"   # chess/extended-A
    "\U0001FA70-\U0001FAFF"   # extended-B
    "\U00002600-\U000026FF"   # misceláneos
    "\U0000200D"              # zero width joiner
    "\U00002B50"              # estrella
    "]+"
)

# Frases que delatan a la IA "asistente"
def post_process_reply(text: str) -> str:
    if not text:
        return "me quedé en blanco, pregunta de nuevo"

    # Limpieza básica
    text = text.lower().strip()
    text = _EMOJI_RE.sub("", text).strip()
    text = text.replace("\n", " ").replace("  ", " ")

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

def sanitize_message_for_chat(content: str, bot_user_id: int | None) -> str:
    text = (content or "").strip()
    if bot_user_id:
        text = text.replace(f"<@{bot_user_id}>", "").replace(f"<@!{bot_user_id}>", "")
    return text.strip()


_ANSI_ESCAPE_RE = re.compile(r'(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]')
_ANSI_BRACKET_RE = re.compile(r'\]\d*;[^\]]*')
_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)
_DISCORD_MENTIONS_RE = re.compile(r'<@!?\d+>|<#\d+>|<@&\d+>')


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
    if len(t.split()) < 4:
        return None
    return t


async def build_markov_model(guild_id: int, channel_id: int) -> markovify.Text | None:
    key = (guild_id, channel_id)
    cached = _markov_cache.get(key)
    if cached is not None:
        return cached

    corpus = await get_corpus_messages(guild_id, limit=500)
    if len(corpus) < 50:
        return None

    text = "\n".join(corpus)
    try:
        model = markovify.Text(text, state_size=1, well_formed=False)
    except Exception:
        return None

    _markov_cache[key] = model
    return model


async def generate_markov_reply(guild_id: int, channel_id: int) -> str | None:
    model = await build_markov_model(guild_id, channel_id)
    if not model:
        return None

    try:
        sentence = model.make_short_sentence(max_chars=60, tries=50)
    except Exception:
        sentence = None

    if sentence:
        return sentence
    return None


# --- EVENTOS PRINCIPALES ---
@bot.event
async def on_ready():
    await init_db()

    try:
        print("--- Iniciando Sincronización de Comandos ---")

        if GUILD_ID_ENV:
            # Sync instantáneo a un servidor específico (desarrollo)
            guild_obj = discord.Object(id=int(GUILD_ID_ENV))
            bot.tree.copy_global_to(guild=guild_obj)
            synced = await bot.tree.sync(guild=guild_obj)
            print(f"✅ Sync al servidor {GUILD_ID_ENV}: {[c.name for c in synced]}")
        else:
            # Sync global (puede tardar hasta 1 hora en propagarse)
            synced = await bot.tree.sync()
            print(f"✅ Sync global: {[c.name for c in synced]}")

    except Exception as e:
        print(f"❌ Error en la sincronización: {e}")

    print(f"🚀 Bot listo como {bot.user}")

@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ No tienes permisos para usar este comando. Requiere `Gestionar servidor`.")
        return
    elif isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Faltan argumentos. Revisa cómo usar el comando.")
        return
    print(f"[ERROR Comando] {getattr(ctx, 'command', None)}: {error}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
        
    # 1. Procesar comandos básicos (!ping, !chat)
    await bot.process_commands(message)
    if (message.content or "").strip().startswith("!"):
        return

    if message.guild:
        # Guardar GIFs (tenor/giphy) si aparecen en el mensaje
        if message.content:
            for m in _GIF_RE.finditer(message.content):
                try:
                    await save_gif_url(message.guild.id, m.group(0))
                except Exception:
                    pass

        for attachment in message.attachments:
            if attachment.url and (
                attachment.url.lower().endswith('.gif') or
                (attachment.content_type and 'gif' in attachment.content_type)
            ):
                try:
                    await save_gif_url(message.guild.id, attachment.url)
                except Exception:
                    pass

        cleaned = clean_for_corpus(message.content or "")
        inserted = False
        if cleaned is not None:
            inserted = await save_corpus_message(message.guild.id, message.channel.id, cleaned)

        auto_generate = False
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
                    pass

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
                if random.random() < 0.15:
                    gif_url = await get_random_gif(message.guild.id)
                    if gif_url:
                        await message.channel.send(gif_url)
                        return
                reply = await generate_markov_reply(message.guild.id, message.channel.id)
                if reply is not None:
                    reply = post_process_reply(reply)
                    for chunk in chunk_message(reply):
                        await message.channel.send(chunk)
            except Exception:
                pass
        return

    if not message.guild:
        return
        
    # 3. Respetar restricciones de canal y modo de chat
    settings = await get_chat_settings(message.guild.id)
    if not settings["enabled"]:
        return
    if settings["channel_id"] and message.channel.id != settings["channel_id"]:
        return

    async with message.channel.typing():
        reply = await generate_markov_reply(message.guild.id, message.channel.id)
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

    saved = 0

    last_msg_id: int | None = None
    while True:
        before_obj = discord.Object(id=last_msg_id) if last_msg_id else None
        batch = [msg async for msg in channel.history(limit=100, before=before_obj, oldest_first=False)]
        if not batch:
            break

        for msg in batch:
            if msg.author.bot:
                continue

            if msg.content:
                for m in _GIF_RE.finditer(msg.content):
                    try:
                        await save_gif_url(interaction.guild.id, m.group(0))
                    except Exception:
                        pass

            for attachment in msg.attachments:
                if attachment.url and (
                    attachment.url.lower().endswith('.gif') or
                    (attachment.content_type and 'gif' in attachment.content_type)
                ):
                    try:
                        await save_gif_url(interaction.guild.id, attachment.url)
                    except Exception:
                        pass

            cleaned = clean_for_corpus(msg.content or "")
            if cleaned is None:
                continue
            if await save_corpus_message(interaction.guild.id, msg.channel.id, cleaned):
                saved += 1
                _note_corpus_insert(interaction.guild.id, msg.channel.id)

        last_msg_id = batch[-1].id

    await interaction.followup.send(f"✅ Guardados {saved} mensajes en el corpus.")


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

    for channel in interaction.guild.text_channels:
        perms = channel.permissions_for(me)
        if not (perms.read_messages and perms.read_message_history):
            continue

        saved = 0
        last_msg_id: int | None = None
        while True:
            before_obj = discord.Object(id=last_msg_id) if last_msg_id else None
            batch = [msg async for msg in channel.history(limit=100, before=before_obj, oldest_first=False)]
            if not batch:
                break

            for msg in batch:
                if msg.author.bot:
                    continue

                if msg.content:
                    for m in _GIF_RE.finditer(msg.content):
                        try:
                            await save_gif_url(interaction.guild.id, m.group(0))
                        except Exception:
                            pass

                for attachment in msg.attachments:
                    if attachment.url and (
                        attachment.url.lower().endswith('.gif') or
                        (attachment.content_type and 'gif' in attachment.content_type)
                    ):
                        try:
                            await save_gif_url(interaction.guild.id, attachment.url)
                        except Exception:
                            pass

                cleaned = clean_for_corpus(msg.content or "")
                if cleaned is None:
                    continue
                if await save_corpus_message(interaction.guild.id, msg.channel.id, cleaned):
                    saved += 1
                    _note_corpus_insert(interaction.guild.id, msg.channel.id)

            last_msg_id = batch[-1].id

        total_saved += saved
        await interaction.followup.send(f"✅ {channel.mention}: guardados {saved} mensajes.", ephemeral=True)

    await interaction.followup.send(f"✅ Refeed_all completado. Total guardado: {total_saved} mensajes.")


@bot.tree.command(name="generar", description="Genera un mensaje usando el modelo Markov del canal.")
async def generar_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    reply = await generate_markov_reply(interaction.guild.id, interaction.channel.id)
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
    if not interaction.user.guild_permissions.manage_guild:
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
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return

    await wipe_corpus(interaction.guild.id)

    # Limpiar caches por guild
    gid = interaction.guild.id
    for key in [k for k in _markov_cache.keys() if k[0] == gid]:
        _markov_cache.pop(key, None)
    for key in [k for k in _corpus_insert_counter.keys() if k[0] == gid]:
        _corpus_insert_counter.pop(key, None)

    await interaction.response.send_message("🗑️ Corpus limpiado. Corre /refeed_all para repoblarlo.")


@bot.tree.command(name="corpus_info", description="Muestra cuántos mensajes hay en el corpus del canal actual.")
async def corpus_info_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return

    count = await count_corpus_messages(interaction.guild.id, interaction.channel.id)
    msg = f"📊 El corpus de este canal tiene {count} mensajes."
    if count < 50:
        msg += "\n⚠️ Necesita al menos 50 mensajes para generar bien."
    await interaction.response.send_message(msg)


if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except discord.errors.LoginFailure:
        print("[ERROR] Token inválido. Verifica DISCORD_TOKEN en .env.")
        sys.exit(1)
