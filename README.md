# Bot de Discord (Python)

Proyecto base para un bot de Discord usando `discord.py`, `python-dotenv` y `aiosqlite`, con chat LLM vía Google Gemini API.

## Requisitos
- Python 3.11+ instalado (Windows: `py` disponible)
- Token de bot desde el [Discord Developer Portal](https://discord.com/developers/applications)
- Intents: habilita **Message Content Intent** y **Server Members Intent** si los necesitas.
 - Clave de Gemini si deseas chat LLM (`GOOGLE_API_KEY`).

## Configuración rápida

```powershell
# 1) Crear y activar entorno virtual (Windows PowerShell)
py -m venv .venv
.venv\Scripts\Activate.ps1

# 2) Instalar dependencias
python -m pip install --upgrade pip
pip install -r requirements.txt

# 3) Variables de entorno
Copy-Item .env.example .env
# Edita .env y coloca tu DISCORD_TOKEN
# Si usarás comandos por prefijo (!), en el Developer Portal activa "Message Content Intent".
# Alternativamente, puedes desactivar el intent poniendo ENABLE_MESSAGE_CONTENT=false (el bot arrancará, pero no funcionarán los !comandos).
 # Para el chat LLM, añade GOOGLE_API_KEY y opcionalmente GEMINI_MODEL (ej: gemini-2.0-flash).

# 4) Ejecutar el bot
python src/bot.py
```

Si ves un mensaje indicando que falta `DISCORD_TOKEN`, revisa tu archivo `.env`.
Si aparece un error de intents privilegiados, activa "Message Content Intent" o cambia `ENABLE_MESSAGE_CONTENT=false` en `.env`.

## Estructura
- `src/bot.py`: código principal del bot
- `src/db.py`: helpers de SQLite (uso de comandos, persona, chat mode)
- `src/llm.py`: cliente Gemini
- `.env.example`: plantilla para variables de entorno
- `requirements.txt`: dependencias
- `.gitignore`: archivos ignorados

## Siguientes pasos
Configura la persona del bot:
- `!persona set name <nombre>`
- `!persona set lore <texto>`
- `!persona set personality <rasgos>`
- `!persona set greeting <saludo>`
- `!persona view`

Chat:
- `!chat <mensaje>`: responde con el LLM usando la persona.
- `!chatmode on|off [channel_id]`: activa respuestas automáticas cuando mencionas al bot o usas su nombre.

Slash Commands (funcionan incluso sin Message Content Intent):
- `/chat <mensaje>`: charla con la persona.
- `/chatmode <estado> [canal]`: activa/desactiva auto-reply y el canal destino.
- `/persona_view`: ver configuración actual.
- `/persona_set_name|_lore|_personality|_greeting`: actualizar campos.

Si los slash commands no aparecen de inmediato:
- Asegúrate de reinvitar el bot con el scope `applications.commands`.
- Pon `GUILD_ID=<id-del-servidor>` en `.env` para sincronizar comandos solo en tu servidor y verlos al instante.

Cuéntame qué más funciones quieres y las implemento.
