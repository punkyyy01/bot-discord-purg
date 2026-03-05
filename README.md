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
Configura la persona del bot usando slash commands:

### Gestión de Personalidades
- `/persona_create`: Abre un formulario para crear un nuevo personaje (nombre, lore, personalidad, acento/frases).
- `/persona_menu`: Menú desplegable para cambiar la personalidad activa del bot.
- `/persona_edit`: Edita los campos de una personalidad existente.
- `/persona_delete`: Elimina una personalidad (excepto la default).
- `/persona_view`: Muestra la configuración de la personalidad activa.

### Chat
- `!chat <mensaje>`: Igual, pero por comando de prefijo.
- `!chat clear`: Borra la memoria de conversación (barrera de contexto).
- `/chatmode <estado> [canal]`: Activa/desactiva auto-reply al mencionar o responder al bot. Opcionalmente restringe a un canal.

### Auto-reply
Cuando el chatmode está activado, el bot responde automáticamente si:
- Lo mencionas con @bot.
- Respondes (reply) a un mensaje del bot.

### Otros
- `!ping`: Verifica que el bot esté online.

Si los slash commands no aparecen de inmediato:
- Asegúrate de reinvitar el bot con el scope `applications.commands`.
- Pon `GUILD_ID=<id-del-servidor>` en `.env` para sincronizar comandos solo en tu servidor y verlos al instante.
