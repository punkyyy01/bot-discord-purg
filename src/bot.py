import os
import sys
import re
import random
import aiohttp

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from db import (
    init_db,
    close_db,
    increment_command_usage,
    get_persona,
    get_persona_profile,
    list_persona_profiles,
    create_persona_profile,
    activate_persona_profile,
    delete_persona_profile,
    update_persona_profile,
    set_chat_mode,
    get_chat_settings,
    top_usage,
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

# Define `_CONECTORES_FINALES` at the top of the file to avoid undefined variable errors.
_CONECTORES_FINALES = [
    " y", " o", " con", " pero", " de", " para", " a", " que", " entonces", " como"
]

# Move the `build_system_prompt` function to the top of the file, before its first usage.

def build_system_prompt(persona: dict) -> str:
    """
    Genera un system prompt dinámico integrando la historia, acento 
    y los parámetros numéricos de actitud del personaje.
    """
    name = persona.get("name") or "Usuario"
    lore = persona.get("lore") or ""
    personality = persona.get("personality") or ""
    accent = persona.get("accent") or ""
    catchphrases = persona.get("catchphrases") or ""

    sarcasmo = persona.get("sarcasmo", 5)
    empatia = persona.get("empatia", 5)
    hostilidad = persona.get("hostilidad", 5)
    humor = persona.get("humor", 5)
    jerga = persona.get("jerga", 5)
    concision = persona.get("concision", 5)

    sections = [
        f"Adopta la siguiente personalidad y responde SIEMPRE como este personaje. Eres {name}."
    ]

    if lore: sections.append(f"[TU HISTORIA]\n{lore}")
    if personality: sections.append(f"[TU PERSONALIDAD]\n{personality}")
    if accent: sections.append(f"[TU FORMA DE HABLAR]\n{accent}")
    if catchphrases: sections.append(f"[FRASES TÍPICAS]\n{catchphrases}")

    sections.append(
        f"[TUS PARÁMETROS DE ACTITUD (Escala 1 al 10)]\n"
        f"- Sarcasmo: {sarcasmo}\n"
        f"- Empatía: {empatia}\n"
        f"- Hostilidad: {hostilidad}\n"
        f"- Humor: {humor}\n"
        f"- Jerga: {jerga}\n"
        f"- Concisión (1=Textos largos, 10=Respuestas ultra cortas): {concision}"
    )

    sections.append(
        "REGLAS ABSOLUTAS:\n"
        "1. NUNCA rompas el personaje.\n"
        "2. NUNCA digas que eres una IA, un asistente o un modelo de lenguaje.\n"
        "3. Responde de forma natural, directa y como si estuvieras en un chat de Discord con amigos.\n"
        "4. Si tu concisión es alta (7 o más), usa frases muy cortas. Evita párrafos largos.\n"
        "5. NO uses comillas, ni expliques tu razonamiento. Escribe tu mensaje y ya."
    )

    return "\n\n".join(sections)

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

_FALLBACK_DEFLECTIONS = [
    "ni idea la verdad",
    "ahora mismo no sé, dímelo tú",
    "qué pereza pensar en eso ahora",
    "eso no tiene sentido bro",
    "paso de responder eso xd"
]

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

    # Detectar frases de IA "asistente"
    text_check = text.lower()
    if any(cop in text_check for cop in _AI_COP_OUT):
        text = random.choice(_FALLBACK_DEFLECTIONS)

    if not text.strip():
        text = "no sé xd"

    return text.strip()

def sanitize_message_for_chat(content: str, bot_user_id: int | None) -> str:
    text = (content or "").strip()
    if bot_user_id:
        text = text.replace(f"<@{bot_user_id}>", "").replace(f"<@!{bot_user_id}>", "")
    return text.strip()

async def download_image_url(url: str) -> dict | None:
    """Descarga una imagen desde cualquier URL y la formatea para Gemini."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    content_type = resp.headers.get('Content-Type', '')
                    if content_type.startswith('image/'):
                        data = await resp.read()
                        return {"mime_type": content_type, "data": data}
        except Exception as e:
            print(f"[ERROR] No se pudo descargar imagen {url}: {e}")
    return None

async def get_all_images_from_message(msg: discord.Message) -> list[dict]:
    """Extrae imágenes tanto de adjuntos directos como de Embeds."""
    images = []
    # 1. Archivos adjuntos directos
    for att in msg.attachments:
        if att.content_type and att.content_type.startswith("image/"):
            img_dict = await download_image_url(att.url)
            if img_dict:
                images.append(img_dict)
    
    # 2. Imágenes dentro de Embeds (Links, bots, Twitter, Tenor, etc.)
    for embed in msg.embeds:
        url = None
        if embed.image and embed.image.url:
            url = embed.image.url
        elif embed.thumbnail and embed.thumbnail.url:
            url = embed.thumbnail.url
            
        if url:
            img_dict = await download_image_url(url)
            if img_dict:
                images.append(img_dict)
                
    return images

async def build_recent_context(message: discord.Message, limit: int = 10) -> list[dict]:
    history_rows: list[dict] = []
    bot_user_id = bot.user.id if bot.user else None

    async for row in message.channel.history(limit=50, before=message, oldest_first=False):
        text = (row.content or "").strip()

        if text.startswith("!chat clear") or text.startswith("🧹"):
            break

        if not text and not row.attachments: # <-- Modificado para no ignorar mensajes con solo fotos
            continue
        if text.startswith("!"):
            continue
        if row.author.bot and (not bot_user_id or row.author.id != bot.user.id):
            continue

        role = "assistant" if (bot_user_id and row.author.id == bot.user.id) else "user"
        clean_text = sanitize_message_for_chat(text, bot_user_id)

        if not clean_text and not row.attachments:
            continue

        if role == "user" and clean_text:
            clean_text = f"[{row.author.display_name}] dijo: {clean_text}"

        images = await get_all_images_from_message(row)

        # Modificamos la condición para saltar mensajes vacíos
        if not clean_text and not images:
            continue

        history_rows.append({"role": role, "content": clean_text, "images": images})

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
        
    # 1. Procesar comandos básicos (!ping, !chat)
    await bot.process_commands(message)
    if (message.content or "").strip().startswith("!"):
        return

    # 2. Verificar si el bot fue mencionado o si le respondieron a él directamente
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
        
    # 3. Respetar restricciones de canal y modo de chat
    settings = await get_chat_settings(message.guild.id)
    if not settings["enabled"]:
        return
    if settings["channel_id"] and message.channel.id != settings["channel_id"]:
        return

    # 4. Limpiar texto de menciones y comandos de memoria
    current_text = sanitize_message_for_chat(message.content or "", bot.user.id if bot.user else None)
    
    _lower = current_text.lower()
    if any(kw in _lower for kw in ("borra cache", "borra caché", "clear cache", "borra memoria", "reset memoria")):
        await message.reply("🧹 ¡Listo! Memoria borrada. Empecemos de cero.")
        return

    # Preparar el texto y las imágenes del mensaje actual
    current_images = await get_all_images_from_message(message)

    # Magia nueva: Si estás respondiendo a otro mensaje, ¡extrae la imagen de ahí también!
    if message.reference and message.reference.resolved and isinstance(message.reference.resolved, discord.Message):
        ref_images = await get_all_images_from_message(message.reference.resolved)
        current_images.extend(ref_images)

    p = await get_persona(message.guild.id)
    context_history = await build_recent_context(message, limit=10)

    messages = [
        {"role": "system", "content": build_system_prompt(p)},
        *context_history,
        {"role": "user", "content": current_text, "images": current_images},
    ]

    async with message.channel.typing():
        try:
            reply = await llm.chat(messages, 0.6, 1000)
            reply = post_process_reply(reply) # Limpiamos la respuesta (emojis, etc)
        except Exception as e:
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
            reply = await llm.chat(messages, 0.6, 1000)
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
            placeholder="Describe el trasfondo del personaje (mín. 20 caracteres)",
            required=True,
            min_length=20,
            max_length=1000,
        )
        self.personalidad = discord.ui.TextInput(
            label="Personalidad",
            style=discord.TextStyle.paragraph,
            placeholder="Describe cómo es el personaje (mín. 20 caracteres)",
            required=True,
            min_length=20,
            max_length=1000,
        )
        self.frases_tipicas = discord.ui.TextInput(
            label="Acento & Frases típicas",
            style=discord.TextStyle.paragraph,
            placeholder="Ej: Habla con acento chileno, dice 'po' y 'weón'. Es agresivo e insultante.",
            required=True,
            min_length=20,
            max_length=1000,
        )

        self.add_item(self.profile_id)
        self.add_item(self.nombre)
        self.add_item(self.lore)
        self.add_item(self.personalidad)
        self.add_item(self.frases_tipicas)

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        profile_id = self.profile_id.value.strip().lower().replace(" ", "_")

        # Concatenar texto descriptivo para generar parámetros numéricos
        texto_completo = " ".join(filter(None, [
            self.lore.value,
            self.personalidad.value,
            self.frases_tipicas.value,
        ]))
        if not texto_completo.strip() or len(texto_completo.strip()) < 10:
            texto_completo = "Este personaje es un usuario promedio de Discord, relajado e informal, pero educado y neutral"
        params = await llm.generar_parametros_persona(texto_completo)

        fields = {
            "name": self.nombre.value or None,
            "lore": self.lore.value or None,
            "personality": self.personalidad.value or None,
            "accent": self.frases_tipicas.value or None,
            "catchphrases": self.frases_tipicas.value or None,
            **params,
        }

        created = await create_persona_profile(guild_id, profile_id, fields=fields, activate=True)
        if not created:
            await interaction.response.send_message(f"⚠️ Ya existe una personalidad con ID `{profile_id}`.", ephemeral=True)
            return

        p_str = ", ".join(f"{k}={params.get(k, 5)}" for k in ("sarcasmo", "empatia", "hostilidad", "humor", "jerga", "concision"))
        await interaction.response.send_message(
            f"✅ Perfil **{self.nombre.value}** (`{profile_id}`) creado y activado.\n"
            f"📊 Parámetros generados: {p_str}",
            ephemeral=True,
        )


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
            placeholder="Describe el trasfondo del personaje (mín. 20 caracteres)",
            required=True,
            min_length=20,
            max_length=1000,
            default=current.get("lore") or "",
        )
        self.personalidad = discord.ui.TextInput(
            label="Personalidad",
            style=discord.TextStyle.paragraph,
            placeholder="Describe cómo es el personaje (mín. 20 caracteres)",
            required=True,
            min_length=20,
            max_length=1000,
            default=current.get("personality") or "",
        )
        self.acento = discord.ui.TextInput(
            label="Acento",
            style=discord.TextStyle.paragraph,
            placeholder="Ej: Habla con acento chileno (mín. 20 caracteres)",
            required=True,
            min_length=20,
            max_length=1000,
            default=current.get("accent") or "",
        )
        self.frases_tipicas = discord.ui.TextInput(
            label="Frases típicas",
            style=discord.TextStyle.paragraph,
            placeholder="Ej: Usa 'po' seguido. (mín. 20 caracteres)",
            required=True,
            min_length=20,
            max_length=1000,
            default=current.get("catchphrases") or "",
        )

        self.add_item(self.nombre)
        self.add_item(self.lore)
        self.add_item(self.personalidad)
        self.add_item(self.acento)
        self.add_item(self.frases_tipicas)

    async def on_submit(self, interaction: discord.Interaction):
        # Concatenar texto descriptivo para regenerar parámetros numéricos
        texto_completo = " ".join(filter(None, [
            self.lore.value,
            self.personalidad.value,
            self.acento.value,
            self.frases_tipicas.value,
        ]))
        if not texto_completo.strip() or len(texto_completo.strip()) < 10:
            texto_completo = "Este personaje es un usuario promedio de Discord, relajado e informal, pero educado y neutral"
        params = await llm.generar_parametros_persona(texto_completo)

        fields = {
            "name": self.nombre.value or None,
            "lore": self.lore.value or None,
            "personality": self.personalidad.value or None,
            "accent": self.acento.value or None,
            "catchphrases": self.frases_tipicas.value or None,
            **params,
        }
        await update_persona_profile(self._guild_id, self._profile_id, fields)
        display_name = self.nombre.value or self._profile_id

        p_str = ", ".join(f"{k}={params.get(k, 5)}" for k in ("sarcasmo", "empatia", "hostilidad", "humor", "jerga", "concision"))
        await interaction.response.send_message(
            f"✅ Personalidad **{display_name}** (`{self._profile_id}`) actualizada.\n"
            f"📊 Parámetros regenerados: {p_str}",
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


@bot.tree.command(name="persona_view", description="Muestra la personalidad activa del bot en este servidor.")
async def persona_view_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Solo en servidores.", ephemeral=True)
        return

    p = await get_persona(interaction.guild.id)
    pid = p.get("profile_id", "default")
    name = p.get("name") or "(sin nombre)"
    lore = p.get("lore") or "(sin lore)"
    personality = p.get("personality") or "(sin personalidad)"
    accent = p.get("accent") or "(sin acento)"
    catchphrases = p.get("catchphrases") or "(sin frases)"
    greeting = p.get("greeting") or "(sin saludo)"

    embed = discord.Embed(title=f"🤖 Persona activa: {name}", color=0x5865F2)
    embed.add_field(name="ID del perfil", value=pid, inline=True)
    embed.add_field(name="Nombre", value=name, inline=True)
    embed.add_field(name="Lore", value=lore[:1024], inline=False)
    embed.add_field(name="Personalidad", value=personality[:1024], inline=False)
    embed.add_field(name="Acento", value=accent[:1024], inline=False)
    embed.add_field(name="Frases típicas", value=catchphrases[:1024], inline=False)
    embed.add_field(name="Saludo", value=greeting[:1024], inline=True)

    params_str = (
        f"Sarcasmo: {p.get('sarcasmo', 5)}/10 | "
        f"Empatía: {p.get('empatia', 5)}/10 | "
        f"Hostilidad: {p.get('hostilidad', 5)}/10 | "
        f"Humor: {p.get('humor', 5)}/10 | "
        f"Jerga: {p.get('jerga', 5)}/10 | "
        f"Concisión: {p.get('concision', 5)}/10"
    )
    embed.add_field(name="📊 Parámetros", value=params_str, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="stats", description="Muestra las estadísticas de uso de los comandos.")
async def stats_slash(interaction: discord.Interaction):
    usage = await top_usage(5)
    if not usage:
        await interaction.response.send_message("Aún no hay datos de uso.", ephemeral=True)
        return

    embed = discord.Embed(title="📊 Estadísticas de Uso", color=0x00ff00)
    descripcion = ""
    for cmd, count in usage:
        descripcion += f"**!{cmd}**: {count} veces\n"
    
    embed.description = descripcion
    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except discord.errors.LoginFailure:
        print("[ERROR] Token inválido. Verifica DISCORD_TOKEN en .env.")
        sys.exit(1)
