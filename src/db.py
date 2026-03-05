import os
import asyncio
import aiosqlite

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "bot.db")

# Conexión global persistente
_db: aiosqlite.Connection | None = None

# Lock para serializar operaciones de escritura compuestas
_db_lock = asyncio.Lock()

# Cache de guilds con perfil default ya creado
guild_cache = set()

async def get_db() -> aiosqlite.Connection:
    """Devuelve la conexión global. Debe llamarse después de init_db()."""
    if _db is None:
        raise RuntimeError("Base de datos no inicializada. Llama a init_db() primero.")
    return _db

SCHEMA = """
CREATE TABLE IF NOT EXISTS command_usage (
    command TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS persona (
    guild_id INTEGER PRIMARY KEY,
    name TEXT,
    lore TEXT,
    personality TEXT,
    greeting TEXT
);
CREATE TABLE IF NOT EXISTS persona_profiles (
    guild_id INTEGER NOT NULL,
    profile_id TEXT NOT NULL,
    name TEXT,
    lore TEXT,
    personality TEXT,
    accent TEXT,
    catchphrases TEXT,
    greeting TEXT,
    sarcasmo INTEGER NOT NULL DEFAULT 5,
    empatia INTEGER NOT NULL DEFAULT 5,
    hostilidad INTEGER NOT NULL DEFAULT 5,
    humor INTEGER NOT NULL DEFAULT 5,
    jerga INTEGER NOT NULL DEFAULT 5,
    concision INTEGER NOT NULL DEFAULT 5,
    is_active INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, profile_id)
);
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER PRIMARY KEY,
    chat_mode_enabled INTEGER NOT NULL DEFAULT 1,
    chat_channel_id INTEGER
);
"""

async def init_db():
    """Inicializa la conexión global y crea las tablas."""
    global _db
    os.makedirs(DATA_DIR, exist_ok=True)
    _db = await aiosqlite.connect(DB_PATH)
    # Activar modo WAL para mejor concurrencia
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    # Crear tablas
    await _db.executescript(SCHEMA)
    await _migrate_legacy_persona(_db)
    await _migrate_add_param_columns(_db)
    await _db.commit()


async def close_db():
    """Cierra la conexión global. Llamar al apagar el bot."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None

async def _migrate_add_param_columns(db: aiosqlite.Connection):
    """Agrega columnas de parámetros numéricos si no existen (migración)."""
    async with db.execute("PRAGMA table_info(persona_profiles)") as cur:
        existing = {row[1] for row in await cur.fetchall()}
    for col in ("sarcasmo", "empatia", "hostilidad", "humor", "jerga", "concision"):
        if col not in existing:
            await db.execute(
                f"ALTER TABLE persona_profiles ADD COLUMN {col} INTEGER NOT NULL DEFAULT 5"
            )


async def _migrate_legacy_persona(db: aiosqlite.Connection):
    async with db.execute(
        "SELECT guild_id, name, lore, personality, greeting FROM persona"
    ) as cursor:
        rows = await cursor.fetchall()

    for guild_id, name, lore, personality, greeting in rows:
        await db.execute(
            "INSERT INTO persona_profiles (guild_id, profile_id, name, lore, personality, accent, catchphrases, greeting, is_active) "
            "VALUES (?, 'default', ?, ?, ?, NULL, NULL, ?, 1) "
            "ON CONFLICT(guild_id, profile_id) DO NOTHING",
            (guild_id, name, lore, personality, greeting),
        )

    async with db.execute(
        "SELECT guild_id FROM persona_profiles GROUP BY guild_id"
    ) as cursor:
        guilds = [row[0] for row in await cursor.fetchall()]

    for guild_id in guilds:
        async with db.execute(
            "SELECT profile_id FROM persona_profiles WHERE guild_id=? AND is_active=1 LIMIT 1",
            (guild_id,),
        ) as cursor:
            active_row = await cursor.fetchone()
        if not active_row:
            await db.execute(
                "UPDATE persona_profiles SET is_active=1 WHERE guild_id=? AND profile_id='default'",
                (guild_id,),
            )

async def _ensure_default_profile(db: aiosqlite.Connection, guild_id: int):
    """Inserta perfil default si no existe. NO hace commit — el llamador es responsable."""
    if guild_id in guild_cache:
        return
    await db.execute(
        "INSERT INTO persona_profiles (guild_id, profile_id, name, lore, personality, accent, catchphrases, greeting, is_active) "
        "VALUES (?, 'default', NULL, NULL, NULL, NULL, NULL, NULL, 1) "
        "ON CONFLICT(guild_id, profile_id) DO NOTHING",
        (guild_id,),
    )
    guild_cache.add(guild_id)


async def _ensure_default_committed(guild_id: int):
    """Versión con lock+commit propia, para funciones de solo lectura."""
    if guild_id in guild_cache:
        return
    db = await get_db()
    async with _db_lock:
        await _ensure_default_profile(db, guild_id)
        await db.commit()

async def increment_command_usage(command: str):
    db = await get_db()
    await db.execute(
        "INSERT INTO command_usage (command, count) VALUES (?, 1) "
        "ON CONFLICT(command) DO UPDATE SET count=count+1",
        (command,),
    )
    await db.commit()

async def top_usage(limit: int = 5):
    db = await get_db()
    async with db.execute(
        "SELECT command, count FROM command_usage ORDER BY count DESC LIMIT ?",
        (limit,),
    ) as cursor:
        return await cursor.fetchall()

# Persona helpers
async def get_persona(guild_id: int):
    await _ensure_default_committed(guild_id)
    db = await get_db()
    async with db.execute(
        "SELECT profile_id, name, lore, personality, accent, catchphrases, greeting, "
        "sarcasmo, empatia, hostilidad, humor, jerga, concision "
        "FROM persona_profiles WHERE guild_id=? ORDER BY is_active DESC, profile_id ASC LIMIT 1",
        (guild_id,),
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return {
                "profile_id": "default",
                "name": None,
                "lore": None,
                "personality": None,
                "accent": None,
                "catchphrases": None,
                "greeting": None,
                "sarcasmo": 5,
                "empatia": 5,
                "hostilidad": 5,
                "humor": 5,
                "jerga": 5,
                "concision": 5,
            }
        return {
            "profile_id": row[0],
            "name": row[1],
            "lore": row[2],
            "personality": row[3],
            "accent": row[4],
            "catchphrases": row[5],
            "greeting": row[6],
            "sarcasmo": row[7],
            "empatia": row[8],
            "hostilidad": row[9],
            "humor": row[10],
            "jerga": row[11],
            "concision": row[12],
        }

async def list_persona_profiles(guild_id: int):
    await _ensure_default_committed(guild_id)
    db = await get_db()
    async with db.execute(
        "SELECT profile_id, COALESCE(name, ''), is_active FROM persona_profiles WHERE guild_id=? ORDER BY profile_id ASC",
        (guild_id,),
    ) as cursor:
        return await cursor.fetchall()

async def create_persona_profile(
    guild_id: int,
    profile_id: str,
    *,
    fields: dict | None = None,
    activate: bool = False,
):
    db = await get_db()
    f = fields or {}
    async with _db_lock:
        await _ensure_default_profile(db, guild_id)
        try:
            await db.execute(
                "INSERT INTO persona_profiles "
                "(guild_id, profile_id, name, lore, personality, accent, catchphrases, greeting, "
                "sarcasmo, empatia, hostilidad, humor, jerga, concision, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    guild_id, profile_id,
                    f.get("name"), f.get("lore"), f.get("personality"),
                    f.get("accent"), f.get("catchphrases"), f.get("greeting"),
                    f.get("sarcasmo", 5), f.get("empatia", 5),
                    f.get("hostilidad", 5), f.get("humor", 5), f.get("jerga", 5),
                    f.get("concision", 5),
                ),
            )
            if activate:
                await db.execute(
                    "UPDATE persona_profiles SET is_active=0 WHERE guild_id=?",
                    (guild_id,),
                )
                await db.execute(
                    "UPDATE persona_profiles SET is_active=1 WHERE guild_id=? AND profile_id=?",
                    (guild_id, profile_id),
                )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            await db.rollback()
            return False

async def activate_persona_profile(guild_id: int, profile_id: str):
    db = await get_db()
    async with _db_lock:
        await _ensure_default_profile(db, guild_id)
        async with db.execute(
            "SELECT 1 FROM persona_profiles WHERE guild_id=? AND profile_id=?",
            (guild_id, profile_id),
        ) as cursor:
            if not await cursor.fetchone():
                return False
        await db.execute(
            "UPDATE persona_profiles SET is_active=0 WHERE guild_id=?",
            (guild_id,),
        )
        await db.execute(
            "UPDATE persona_profiles SET is_active=1 WHERE guild_id=? AND profile_id=?",
            (guild_id, profile_id),
        )
        await db.commit()
        return True

async def find_persona_profiles(guild_id: int, query: str, limit: int = 5):
    needle = (query or "").strip().lower()
    await _ensure_default_committed(guild_id)
    db = await get_db()
    async with db.execute(
        "SELECT profile_id, COALESCE(name, ''), is_active FROM persona_profiles WHERE guild_id=? ORDER BY is_active DESC, profile_id ASC",
        (guild_id,),
    ) as cursor:
        rows = await cursor.fetchall()

    if not needle:
        return rows[:limit]

    exact_id = [row for row in rows if row[0].lower() == needle]
    if exact_id:
        return exact_id[:1]

    exact_name = [row for row in rows if row[1].strip().lower() == needle]
    if exact_name:
        return exact_name[:1]

    starts_id = [row for row in rows if row[0].lower().startswith(needle)]
    starts_name = [row for row in rows if row[1].strip().lower().startswith(needle)]

    merged: list[tuple[str, str, int]] = []
    for row in starts_id + starts_name:
        if row not in merged:
            merged.append(row)

    if merged:
        return merged[:limit]

    contains = [
        row for row in rows
        if needle in row[0].lower() or needle in row[1].strip().lower()
    ]
    return contains[:limit]

async def delete_persona_profile(guild_id: int, profile_id: str):
    if profile_id == "default":
        return False
    db = await get_db()
    async with _db_lock:
        async with db.execute(
            "SELECT is_active FROM persona_profiles WHERE guild_id=? AND profile_id=?",
            (guild_id, profile_id),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return False
        was_active = row[0]
        await db.execute(
            "DELETE FROM persona_profiles WHERE guild_id=? AND profile_id=?",
            (guild_id, profile_id),
        )
        if was_active:
            await db.execute(
                "UPDATE persona_profiles SET is_active=1 WHERE guild_id=? AND profile_id='default'",
                (guild_id,),
            )
        await db.commit()
        return True

async def set_persona_field(guild_id: int, field: str, value: str | None):
    valid = {"name", "lore", "personality", "accent", "catchphrases", "greeting"}
    if field not in valid:
        raise ValueError("Campo inválido")
    db = await get_db()
    async with _db_lock:
        await _ensure_default_profile(db, guild_id)
        await db.execute(
            f"UPDATE persona_profiles SET {field}=? WHERE guild_id=? AND is_active=1",
            (value, guild_id),
        )
        await db.commit()

async def get_persona_profile(guild_id: int, profile_id: str):
    db = await get_db()
    async with db.execute(
        "SELECT profile_id, name, lore, personality, accent, catchphrases, greeting, "
        "sarcasmo, empatia, hostilidad, humor, jerga, concision "
        "FROM persona_profiles WHERE guild_id=? AND profile_id=?",
        (guild_id, profile_id),
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "profile_id": row[0],
            "name": row[1],
            "lore": row[2],
            "personality": row[3],
            "accent": row[4],
            "catchphrases": row[5],
            "greeting": row[6],
            "sarcasmo": row[7],
            "empatia": row[8],
            "hostilidad": row[9],
            "humor": row[10],
            "jerga": row[11],
            "concision": row[12],
        }

async def update_persona_profile(guild_id: int, profile_id: str, fields: dict):
    valid = {"name", "lore", "personality", "accent", "catchphrases", "greeting",
             "sarcasmo", "empatia", "hostilidad", "humor", "jerga", "concision"}
    to_update = {k: v for k, v in fields.items() if k in valid}
    if not to_update:
        return
    db = await get_db()
    async with _db_lock:
        set_clause = ", ".join(f"{k}=?" for k in to_update)
        values = list(to_update.values()) + [guild_id, profile_id]
        await db.execute(
            f"UPDATE persona_profiles SET {set_clause} WHERE guild_id=? AND profile_id=?",
            values,
        )
        await db.commit()

# Settings helpers
async def set_chat_mode(guild_id: int, enabled: bool, channel_id: int | None = None):
    db = await get_db()
    async with _db_lock:
        await db.execute(
            "INSERT INTO settings (guild_id, chat_mode_enabled, chat_channel_id) VALUES (?, 0, NULL)"
            " ON CONFLICT(guild_id) DO NOTHING",
            (guild_id,),
        )
        await db.execute(
            "UPDATE settings SET chat_mode_enabled=?, chat_channel_id=? WHERE guild_id=?",
            (1 if enabled else 0, channel_id, guild_id),
        )
        await db.commit()

async def get_chat_settings(guild_id: int):
    db = await get_db()
    async with db.execute(
        "SELECT chat_mode_enabled, chat_channel_id FROM settings WHERE guild_id=?",
        (guild_id,),
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return {"enabled": True, "channel_id": None}
        return {"enabled": bool(row[0]), "channel_id": row[1]}
