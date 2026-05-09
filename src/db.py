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

async def get_db() -> aiosqlite.Connection:
    """Devuelve la conexión global. Debe llamarse después de init_db()."""
    if _db is None:
        raise RuntimeError("Base de datos no inicializada. Llama a init_db() primero.")
    return _db

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER PRIMARY KEY,
    chat_mode_enabled INTEGER NOT NULL DEFAULT 1,
    chat_channel_id INTEGER
);

CREATE TABLE IF NOT EXISTS corpus_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id, channel_id, content)
);

CREATE TABLE IF NOT EXISTS corpus_gifs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id, url)
);

CREATE TABLE IF NOT EXISTS youtube_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    youtube_channel_id TEXT NOT NULL,
    youtube_channel_name TEXT NOT NULL,
    last_video_id TEXT,
    discord_channel_id INTEGER NOT NULL,
    UNIQUE(guild_id, youtube_channel_id)
);
"""

async def _migrate_corpus_uniqueness(db: aiosqlite.Connection):
    """Asegura unicidad del corpus por (guild, channel, content).

    Para bases existentes, deduplica y luego crea un índice único (SQLite no permite
    agregar constraints UNIQUE a una tabla existente sin recrearla).
    """
    # Eliminar duplicados existentes (conserva el id más chico por grupo)
    await db.execute(
        "DELETE FROM corpus_messages "
        "WHERE id NOT IN ("
        "  SELECT MIN(id) FROM corpus_messages GROUP BY guild_id, channel_id, content"
        ")"
    )
    # Índice único para garantizar unicidad a nivel DB también en tablas legacy
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS corpus_messages_unique_idx "
        "ON corpus_messages(guild_id, channel_id, content)"
    )

async def init_db():
    """Inicializa la conexión global y crea las tablas."""
    global _db
    if _db is not None:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    _db = await aiosqlite.connect(DB_PATH)
    # Activar modo WAL para mejor concurrencia
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    # Crear tablas
    await _db.executescript(SCHEMA)
    await _migrate_corpus_uniqueness(_db)
    await _db.commit()


async def close_db():
    """Cierra la conexión global. Llamar al apagar el bot."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def _was_inserted(cursor: aiosqlite.Cursor) -> bool:
    if cursor.rowcount == 1:
        return True
    if cursor.rowcount == 0:
        return False
    if cursor.rowcount == -1:
        db = cursor.connection
        async with db.execute("SELECT changes()") as cur:
            row = await cur.fetchone()
        return bool(row and row[0] == 1)
    return False

# Settings helpers
async def set_chat_mode(guild_id: int, enabled: bool, channel_id: int | None = None):
    db = await get_db()
    async with _db_lock:
        await db.execute(
            "INSERT INTO settings (guild_id, chat_mode_enabled, chat_channel_id) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET "
            "    chat_mode_enabled=excluded.chat_mode_enabled, "
            "    chat_channel_id=excluded.chat_channel_id",
            (guild_id, 1 if enabled else 0, channel_id),
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


async def save_corpus_message(guild_id: int, channel_id: int, content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    if len(text.split()) <= 3:
        return False

    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO corpus_messages (guild_id, channel_id, content) VALUES (?, ?, ?)",
            (guild_id, channel_id, text),
        )
        inserted = await _was_inserted(cursor)
        await db.commit()
    return inserted


async def count_corpus_messages(guild_id: int, channel_id: int) -> int:
    db = await get_db()
    async with db.execute(
        "SELECT COUNT(*) FROM corpus_messages WHERE guild_id=? AND channel_id=?",
        (guild_id, channel_id),
    ) as cursor:
        row = await cursor.fetchone()
    return int(row[0] if row else 0)


async def get_corpus_messages(guild_id: int, limit: int = 500) -> list[str]:
    db = await get_db()
    async with db.execute(
        "SELECT content FROM corpus_messages WHERE guild_id=? ORDER BY RANDOM() LIMIT ?",
        (guild_id, limit),
    ) as cursor:
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def wipe_corpus(guild_id: int) -> None:
    db = await get_db()
    async with _db_lock:
        await db.execute(
            "DELETE FROM corpus_messages WHERE guild_id=?",
            (guild_id,),
        )
        await db.commit()


async def save_gif_url(guild_id: int, url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return False

    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO corpus_gifs (guild_id, url) VALUES (?, ?)",
            (guild_id, u),
        )
        inserted = await _was_inserted(cursor)
        await db.commit()
    return inserted


async def get_random_gif(guild_id: int) -> str | None:
    db = await get_db()
    async with db.execute(
        "SELECT url FROM corpus_gifs WHERE guild_id=? ORDER BY RANDOM() LIMIT 1",
        (guild_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None


async def count_gif_urls(guild_id: int) -> int:
    db = await get_db()
    async with db.execute(
        "SELECT COUNT(*) FROM corpus_gifs WHERE guild_id=?",
        (guild_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return int(row[0] if row else 0)


async def add_youtube_sub(
    guild_id: int,
    channel_id: int,
    youtube_channel_id: str,
    youtube_channel_name: str,
    discord_channel_id: int,
) -> bool:
    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO youtube_subscriptions "
            "(guild_id, channel_id, youtube_channel_id, youtube_channel_name, discord_channel_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, channel_id, youtube_channel_id, youtube_channel_name, discord_channel_id),
        )
        inserted = await _was_inserted(cursor)
        await db.commit()
    return inserted


async def remove_youtube_sub(guild_id: int, youtube_channel_id: str) -> bool:
    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "DELETE FROM youtube_subscriptions WHERE guild_id=? AND youtube_channel_id=?",
            (guild_id, youtube_channel_id),
        )
        removed = cursor.rowcount > 0
        await db.commit()
    return removed


async def list_youtube_subs(guild_id: int) -> list[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT id, guild_id, channel_id, youtube_channel_id, youtube_channel_name, last_video_id, discord_channel_id "
        "FROM youtube_subscriptions WHERE guild_id=?",
        (guild_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            "id": r[0],
            "guild_id": r[1],
            "channel_id": r[2],
            "youtube_channel_id": r[3],
            "youtube_channel_name": r[4],
            "last_video_id": r[5],
            "discord_channel_id": r[6],
        }
        for r in rows
    ]


async def get_all_youtube_subs() -> list[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT id, guild_id, channel_id, youtube_channel_id, youtube_channel_name, last_video_id, discord_channel_id "
        "FROM youtube_subscriptions"
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            "id": r[0],
            "guild_id": r[1],
            "channel_id": r[2],
            "youtube_channel_id": r[3],
            "youtube_channel_name": r[4],
            "last_video_id": r[5],
            "discord_channel_id": r[6],
        }
        for r in rows
    ]


async def update_last_video_id(guild_id: int, youtube_channel_id: str, video_id: str) -> None:
    db = await get_db()
    async with _db_lock:
        await db.execute(
            "UPDATE youtube_subscriptions SET last_video_id=? WHERE guild_id=? AND youtube_channel_id=?",
            (video_id, guild_id, youtube_channel_id),
        )
        await db.commit()
