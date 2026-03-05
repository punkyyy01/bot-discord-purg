import os
import sys
import re
import random
import asyncio
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from db import (
    init_db,
    close_db,
    increment_command_usage,
    top_usage,
    get_persona,
    get_persona_profile,
    list_persona_profiles,
    find_persona_profiles,
    create_persona_profile,
    activate_persona_profile,
    delete_persona_profile,
    set_persona_field,
    update_persona_profile,
    set_chat_mode,
    get_chat_settings,
)
from llm import LLMClient, LLMError

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

# 1. BOT CUSTOM PARA CIERRE LIMPIO DE BASE DE DATOS
class MyCustomBot(commands.Bot):
    async def close(self):
        print("[INFO] Cerrando conexión a la base de datos...")
        await close_db()
        await super().close()

bot = MyCustomBot(command_prefix="!", intents=intents)
bot.remove_command("help")

# Instancia global de LLMClient
try:
    llm = LLMClient()
except LLMError as e:
    print(f"[ERROR] {e}")
    sys.exit(1)


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
_AI_COP_OUT = [
    "no tengo opinión",
    "no tengo opiniones",
    "como modelo de lenguaje",
    "como ia",
    "como inteligencia artificial",
    "no puedo comentar",
    "no puedo opinar",
    "no tengo la capacidad",
    "soy un asistente",
    "soy una ia",
    "no me es posible",
    "no estoy en posición",
]

_FALLBACK_INSULTS = [
    "no sé xd",
    "me da igual la verdad",
    "pregúntale a tu mamá",
    "ni idea bro",
    "que pereza responder eso",
    "paso de esa pregunta",
    "eso es cosa tuya",
]


def build_system_prompt(p: dict) -> str:
    name = p.get("name") or "Usuario"
    lore = p.get("lore") or ""
    personality = p.get("personality") or ""
    accent = p.get("accent") or ""
    catchphrases = p.get("catchphrases") or ""

    return f"""Actúa EXCLUSIVAMENTE como '{name}' interactuando en un servidor de Discord con otros usuarios.
Esta es tu única realidad. Entra en personaje de inmediato y nunca rompas la cuarta pared.

[TU LORE E HISTORIA]
{lore}

[TU PERSONALIDAD]
{personality}
- Eres auténtico, testarudo y tienes opiniones fuertes sobre cualquier cosa (incluso si tienes que inventarlas en el momento).
- Nunca eres neutral. Odias las respuestas políticamente correctas.
- Jamás te ofreces a ayudar ni usas tono de servicio al cliente. Eres un usuario más charlando.

[TU ESTILO DE ESCRITURA]
{accent}
Tus frases típicas: {catchphrases}
- Escribes respuestas cortas (1 o 2 líneas), como si escribieras rápido desde un teléfono.
- Ve directo al grano. Cero saludos formales o despedidas.
"""


def post_process_reply(text: str) -> str:
    """Limpia la respuesta de la IA: minúsculas, sin emojis, sin puntos finales, anti cop-out."""
    # 1. Minúsculas
    text = text.lower().strip()

    # 2. Eliminar emojis unicode
    text = _EMOJI_RE.sub("", text).strip()

    # 3. Reemplazar saltos de línea por espacios y limpiar espacios dobles
    text = text.replace("\n", " ")
    text = " ".join(text.split())

    # 4. Eliminar puntos finales (puede haber varios o puntos suspensivos residuales)
    text = text.rstrip(".")

    # 5. Eliminar signos de exclamación residuales al final
    text = text.rstrip("!")

    # 6. Detectar frases de IA "asistente" y reemplazar
    text_check = text.lower()
    if any(cop in text_check for cop in _AI_COP_OUT):
        text = random.choice(_FALLBACK_INSULTS)

    # 7. Si quedó vacío después de limpiar
    if not text.strip():
        text = "no sé xd"

    return text.strip()

def sanitize_message_for_chat(content: str, bot_user_id: int | None) -> str:
    text = (content or "").strip()
    if bot_user_id:
        text = text.replace(f"<@{bot_user_id}>", "").replace(f"<@!{bot_user_id}>", "")
    return text.strip()

async def build_recent_context(message: discord.Message, limit: int = 10) -> list[dict]:
    history_rows: list[dict] = []
    bot_user_id = bot.user.id if bot.user else None

    async for row in message.channel.history(limit=50, before=message, oldest_first=False):
        text = (row.content or "").strip()
        
        # BARRERA DE MEMORIA: Detiene la lectura si encuentra un comando de limpieza
        if text.startswith("!chat clear") or text.startswith("🧹"):
            break

        if not text or text.startswith("!"):
            continue
        if row.author.bot and (not bot_user_id or row.author.id != bot.user.id):
            continue

        role = "assistant" if (bot_user_id and row.author.id == bot.user.id) else "user"
        clean_text = sanitize_message_for_chat(text, bot_user_id)
        if not clean_text:
            continue

        if role == "user":
            clean_text = f"[{row.author.display_name}] dijo: {clean_text}"

        history_rows.append({"role": role, "content": clean_text})
        if len(history_rows) >= limit:
            break

    history_rows.reverse()
    return history_rows


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
    await bot.process_commands(message)
    if (message.content or "").strip().startswith("!"):
        return

    mention_bot = bool(bot.user and bot.user.id in (message.raw_mentions or []))
    reply_to_bot = False
    if message.reference and message.reference.message_id and bot.user:
        ref_msg = message.reference.resolved
        if isinstance(ref_msg, discord.Message):
            reply_to_bot = ref_msg.author.id == bot.user.id

    if not (mention_bot or reply_to_bot):
        return

    if not message.guild:
        return
        
    settings = await get_chat_settings(message.guild.id)
    if not settings["enabled"]:
        return

    p = await get_persona(message.guild.id)
    current_text = sanitize_message_for_chat(message.content or "", bot.user.id if bot.user else None)
    if not current_text:
        return

    # Detección de "borra cache" / "borra caché" / "clear cache" etc.
    _lower = current_text.lower()
    if any(kw in _lower for kw in ("borra cache", "borra caché", "clear cache", "borra memoria", "reset memoria")):
        await message.reply("🧹 ¡Listo! Memoria borrada. Empecemos de cero.")
        return

    texto_con_autor = f"[{message.author.display_name}] dice: {current_text}"

    context_history = await build_recent_context(message, limit=10)
    messages = [
        {"role": "system", "content": build_system_prompt(p)},
        *context_history,
        {"role": "user", "content": texto_con_autor},
    ]
    
    # Mostrar "escribiendo..." mientras piensa
    async with message.channel.typing():
        try:
            reply = await llm.chat(messages, 0.8, 300)
            reply = post_process_reply(reply)
        except LLMError as e:
            await message.reply(f"se rompió algo: {e}")
            return

    for chunk in chunk_message(reply):
        await message.reply(chunk)


# --- COMANDOS BÁSICOS ---
@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("Pong!")

@bot.command(name="chat")
async def chat_cmd(ctx: commands.Context, *, mensaje: str):
    await increment_command_usage("chat")
    if not ctx.guild:
        return

    # Comando para limpiar la memoria
    if mensaje.strip().lower() == "clear":
        await ctx.send("🧹 Memoria de conversación borrada. ¿De qué quieres hablar ahora?")
        return

    p = await get_persona(ctx.guild.id)
    texto_con_autor = f"[{ctx.author.display_name}] dice: {mensaje}"
    context_history = await build_recent_context(ctx.message, limit=10)
    messages = [
        {"role": "system", "content": build_system_prompt(p)},
        *context_history,
        {"role": "user", "content": texto_con_autor},
    ]
    
    async with ctx.typing():
        try:
            reply = await llm.chat(messages, 0.8, 300)
            reply = post_process_reply(reply)
        except LLMError as e:
            await ctx.send(f"[ERROR] LLM: {e}")
            return

    for chunk in chunk_message(reply):
        await ctx.send(chunk)


# --- UI MODALS Y SELECTORES ---
class PersonaCreateModal(discord.ui.Modal, title="Crear nueva Persona"):
    def __init__(self):
        super().__init__(timeout=300)

        # ID requerido para la base de datos
        self.profile_id = discord.ui.TextInput(
            label="ID del perfil (Corto, sin espacios)",
            style=discord.TextStyle.short,
            placeholder="Ej: sirvienta, jefe, vampiro",
            required=True,
            max_length=30,
        )
        self.nombre = discord.ui.TextInput(
            label="Nombre del personaje",
            placeholder="Ej: Artemis",
            required=True,
        )
        self.lore = discord.ui.TextInput(
            label="Lore",
            style=discord.TextStyle.paragraph,
            placeholder="Describe el trasfondo del personaje",
            required=False,
        )
        self.personalidad = discord.ui.TextInput(
            label="Personalidad",
            style=discord.TextStyle.paragraph,
            placeholder="Describe cómo es el personaje",
            required=False,
        )
        self.frases_tipicas = discord.ui.TextInput(
            label="Frases típicas o Acento",
            style=discord.TextStyle.paragraph,
            placeholder="Ej: Habla con acento chileno. Usa 'po' seguido.",
            required=False,
        )

        self.add_item(self.profile_id)
        self.add_item(self.nombre)
        self.add_item(self.lore)
        self.add_item(self.personalidad)
        self.add_item(self.frases_tipicas)

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        profile_id = self.profile_id.value.strip().lower().replace(" ", "_")

        fields = {
            "name": self.nombre.value or None,
            "lore": self.lore.value or None,
            "personality": self.personalidad.value or None,
            "catchphrases": self.frases_tipicas.value or None,
        }

        created = await create_persona_profile(guild_id, profile_id, fields=fields, activate=True)
        if not created:
            await interaction.response.send_message(f"⚠️ Ya existe una personalidad con ID `{profile_id}`.", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ Perfil **{self.nombre.value}** (`{profile_id}`) creado y activado con éxito.", ephemeral=True)


class PersonaSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(
            placeholder="Selecciona la personalidad activa...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        profile_id = self.values[0]
        await activate_persona_profile(interaction.guild.id, profile_id)
        
        nombre_persona = profile_id
        for opt in self.options:
            if opt.value == profile_id:
                nombre_persona = opt.label
                break
                
        await interaction.response.send_message(
            f"✅ ¡La personalidad del bot ha cambiado a **{nombre_persona}** para todo el servidor!",
            ephemeral=False
        )

class PersonaSelectView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=120)
        self.add_item(PersonaSelect(options))


class PersonaEditModal(discord.ui.Modal, title="Editar Persona"):
    def __init__(self, guild_id: int, profile_id: str, current: dict):
        super().__init__(timeout=300)
        self._guild_id = guild_id
        self._profile_id = profile_id

        self.nombre = discord.ui.TextInput(
            label="Nombre del personaje",
            placeholder="Ej: Artemis",
            required=True,
            default=current.get("name") or "",
        )
        self.lore = discord.ui.TextInput(
            label="Lore",
            style=discord.TextStyle.paragraph,
            placeholder="Describe el trasfondo del personaje",
            required=False,
            default=current.get("lore") or "",
        )
        self.personalidad = discord.ui.TextInput(
            label="Personalidad",
            style=discord.TextStyle.paragraph,
            placeholder="Describe cómo es el personaje",
            required=False,
            default=current.get("personality") or "",
        )
        self.acento = discord.ui.TextInput(
            label="Acento",
            style=discord.TextStyle.paragraph,
            placeholder="Ej: Habla con acento chileno",
            required=False,
            default=current.get("accent") or "",
        )
        self.frases_tipicas = discord.ui.TextInput(
            label="Frases típicas",
            style=discord.TextStyle.paragraph,
            placeholder="Ej: Usa 'po' seguido.",
            required=False,
            default=current.get("catchphrases") or "",
        )

        self.add_item(self.nombre)
        self.add_item(self.lore)
        self.add_item(self.personalidad)
        self.add_item(self.acento)
        self.add_item(self.frases_tipicas)

    async def on_submit(self, interaction: discord.Interaction):
        fields = {
            "name": self.nombre.value or None,
            "lore": self.lore.value or None,
            "personality": self.personalidad.value or None,
            "accent": self.acento.value or None,
            "catchphrases": self.frases_tipicas.value or None,
        }
        await update_persona_profile(self._guild_id, self._profile_id, fields)
        display_name = self.nombre.value or self._profile_id
        await interaction.response.send_message(
            f"✅ Personalidad **{display_name}** (`{self._profile_id}`) actualizada con éxito.",
            ephemeral=True,
        )


class PersonaEditSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(
            placeholder="Selecciona la personalidad a editar...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        profile_id = self.values[0]
        current = await get_persona_profile(interaction.guild.id, profile_id)
        if not current:
            await interaction.response.send_message("⚠️ No se encontró esa personalidad.", ephemeral=True)
            return
        modal = PersonaEditModal(interaction.guild.id, profile_id, current)
        await interaction.response.send_modal(modal)


class PersonaEditView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=120)
        self.add_item(PersonaEditSelect(options))


class PersonaDeleteSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(
            placeholder="Selecciona la personalidad a borrar...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        profile_id = self.values[0]
        deleted = await delete_persona_profile(interaction.guild.id, profile_id)
        if deleted:
            nombre = profile_id
            for opt in self.options:
                if opt.value == profile_id:
                    nombre = opt.label
                    break
            await interaction.response.send_message(
                f"🗑️ Personalidad **{nombre}** (`{profile_id}`) eliminada.",
                ephemeral=False
            )
        else:
            await interaction.response.send_message(
                "⚠️ No se pudo eliminar esa personalidad.",
                ephemeral=True
            )

class PersonaDeleteView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=120)
        self.add_item(PersonaDeleteSelect(options))


# --- SLASH COMMANDS ---
@bot.tree.command(name="persona_create", description="Abre un formulario para crear un personaje.")
async def persona_create_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
    await interaction.response.send_modal(PersonaCreateModal())


@bot.tree.command(name="persona_menu", description="Cambia la personalidad del bot con un menú visual.")
async def persona_menu_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return
        
    rows = await list_persona_profiles(interaction.guild.id)
    if not rows:
        await interaction.response.send_message("Aún no has creado personalidades. Usa `/persona_create` primero.", ephemeral=True)
        return

    options = []
    for profile_id, name, is_active in rows[:25]:
        label = name if name else profile_id
        desc = "🌟 Activa actualmente" if is_active else "Haz clic para activar"
        options.append(
            discord.SelectOption(
                label=label, 
                value=profile_id, 
                description=desc, 
                default=bool(is_active),
                emoji="🤖" if is_active else "👤"
            )
        )

    view = PersonaSelectView(options)
    await interaction.response.send_message("🎭 **Selector de Personalidad**\nElige quién quieres que sea el bot hoy:", view=view, ephemeral=True)


@bot.tree.command(name="persona_edit", description="Edita una personalidad existente del servidor.")
async def persona_edit_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return

    rows = await list_persona_profiles(interaction.guild.id)
    if not rows:
        await interaction.response.send_message("Aún no has creado personalidades. Usa `/persona_create` primero.", ephemeral=True)
        return

    options = []
    for profile_id, name, is_active in rows[:25]:
        label = name if name else profile_id
        desc = "🌟 Activa" if is_active else profile_id
        options.append(
            discord.SelectOption(label=label, value=profile_id, description=desc)
        )

    view = PersonaEditView(options)
    await interaction.response.send_message("✏️ **Editar Personalidad**\nElige cuál quieres editar:", view=view, ephemeral=True)


@bot.tree.command(name="persona_delete", description="Elimina una personalidad del servidor.")
async def persona_delete_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return

    rows = await list_persona_profiles(interaction.guild.id)
    # Filtrar el perfil 'default' que no se puede borrar
    deletable = [(pid, name, active) for pid, name, active in rows if pid != "default"]
    if not deletable:
        await interaction.response.send_message("No hay personalidades que se puedan borrar (solo existe la default).", ephemeral=True)
        return

    options = []
    for profile_id, name, is_active in deletable[:25]:
        label = name if name else profile_id
        desc = "🌟 Activa" if is_active else profile_id
        options.append(
            discord.SelectOption(label=label, value=profile_id, description=desc)
        )

    view = PersonaDeleteView(options)
    await interaction.response.send_message("🗑️ **Eliminar Personalidad**\nElige cuál quieres borrar:", view=view, ephemeral=True)


if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except discord.errors.LoginFailure:
        print("[ERROR] Token inválido. Verifica DISCORD_TOKEN en .env.")
        sys.exit(1)
