"""Punto de entrada de Purgito.

Toda la funcionalidad vive en extensiones (src/cogs/); aquí solo se configura
logging, se inicializa la DB y se cargan las extensiones.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import discord
from discord.ext import commands

import config  # ejecuta load_dotenv() al importarse
import r2
from db import close_db, init_db

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

EXTENSIONS = [
    "cogs.premium",   # primero: expone is_premium_guild al resto
    "cogs.chat",
    "cogs.gifs",
    "cogs.memes",
    "cogs.musica",
    "cogs.youtube",
    "cogs.general",
    "cogs.settings",
]

intents = discord.Intents.default()
intents.message_content = config.ENABLE_MESSAGE_CONTENT


class PurgitoBot(commands.Bot):
    async def setup_hook(self) -> None:
        await init_db()
        for extension in EXTENSIONS:
            await self.load_extension(extension)
            log.info("Extensión cargada: %s", extension)

    async def close(self) -> None:
        log.info("Cerrando conexión a la base de datos...")
        await close_db()
        await super().close()


bot = PurgitoBot(command_prefix="!", intents=intents)
bot.remove_command("help")


@bot.event
async def on_ready():
    if not r2.available():
        log.warning(
            "R2 no configurado: las imágenes de Discord CDN se guardarán con su URL original "
            "(pueden expirar). Configura R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, "
            "R2_BUCKET_NAME y R2_PUBLIC_URL para persistencia permanente."
        )

    try:
        log.info("Iniciando sincronización de comandos")

        # Sync global siempre — necesario para que cualquier servidor nuevo reciba los comandos.
        synced = await bot.tree.sync()
        log.info("Sync global: %s", [c.name for c in synced])

        if config.GUILD_ID_ENV:
            # Sync instantáneo adicional a tu servidor de desarrollo (no reemplaza al global).
            guild_obj = discord.Object(id=int(config.GUILD_ID_ENV))
            bot.tree.copy_global_to(guild=guild_obj)
            guild_synced = await bot.tree.sync(guild=guild_obj)
            log.info("Sync instantáneo al servidor %s: %s", config.GUILD_ID_ENV, [c.name for c in guild_synced])

    except Exception:
        log.exception("Error en la sincronización de comandos")

    log.info("Bot listo como %s", bot.user)


if __name__ == "__main__":
    if not config.TOKEN:
        log.critical("Falta DISCORD_TOKEN en .env. Copia .env.example a .env y pon tu token.")
        sys.exit(1)
    try:
        bot.run(config.TOKEN)
    except discord.errors.LoginFailure:
        log.critical("Token inválido. Verifica DISCORD_TOKEN en .env.")
        sys.exit(1)
