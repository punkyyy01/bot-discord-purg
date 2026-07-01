"""Configuración central: variables de entorno y constantes compartidas.

Todos los módulos leen la config desde aquí en vez de hacer os.getenv disperso.
load_dotenv() se ejecuta al importar este módulo, así que basta con importar
config antes que cualquier otro módulo propio.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except Exception:
        return default
    return value if value > 0 else default


TOKEN = os.getenv("DISCORD_TOKEN")
ENABLE_MESSAGE_CONTENT = os.getenv("ENABLE_MESSAGE_CONTENT", "true").strip().lower() in ("1", "true", "yes")
GUILD_ID_ENV = os.getenv("GUILD_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
BOT_TRIGGER_NAME = os.getenv("BOT_TRIGGER_NAME", "artemis").strip().lower()
BOT_OWNER_ID: int | None = int(os.getenv("BOT_OWNER_ID", "0")) or None
# ID fijo del servidor original PURG4TORY — siempre premium, sin pasar por la tabla.
PURGATORY_GUILD_ID = 1434103563214393347
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

REFEED_MAX_MESSAGES = env_int("REFEED_MAX_MESSAGES", 80_000)
REFEED_ALL_MAX_MESSAGES = env_int("REFEED_ALL_MAX_MESSAGES", 20_000)
MARKOV_TRAINING_MESSAGES = env_int("MARKOV_TRAINING_MESSAGES", 5_000)
USER_MARKOV_TRAINING_MESSAGES = env_int("USER_MARKOV_TRAINING_MESSAGES", 2_000)

SPECIAL_PHRASE_PROBABILITY = 0.05
SPECIAL_PHRASE_COOLDOWN = 40 * 60  # 40 minutos en segundos

GROQ_GUILD_COOLDOWN = 10.0

# El bot considera generar un mensaje espontáneo cada AUTO_GENERATE_EVERY
# inserts al corpus de un canal; AUTO_GENERATE_PROBABILITY es el azar extra
# para que no sea puramente determinístico por conteo.
AUTO_GENERATE_EVERY = 15
AUTO_GENERATE_PROBABILITY = float(os.getenv("AUTO_GENERATE_PROBABILITY", "0.6"))

MEME_MAX_BYTES = 10 * 1024 * 1024
