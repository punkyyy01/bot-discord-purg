import os
import asyncio
import logging
import aiosqlite

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "bot.db")

log = logging.getLogger(__name__)

_db: aiosqlite.Connection | None = None
_db_lock = asyncio.Lock()


async def get_db() -> aiosqlite.Connection:
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
    message_id INTEGER,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id, message_id)
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

CREATE TABLE IF NOT EXISTS user_corpus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    author_name TEXT NOT NULL,
    message_id INTEGER,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id, message_id)
);

CREATE TABLE IF NOT EXISTS ignored_channels (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS meme_schedule (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    interval_minutes INTEGER NOT NULL DEFAULT 180,
    last_posted_at TEXT,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS corpus_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id, url)
);
"""


async def init_db():
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
    try:
        await _db.execute("ALTER TABLE youtube_subscriptions ADD COLUMN mention_role_id INTEGER")
        await _db.commit()
    except Exception:
        log.debug("Columna mention_role_id ya existe en youtube_subscriptions")
    await _db.commit()


async def close_db():
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def _was_inserted(cursor: aiosqlite.Cursor) -> bool:
    return cursor.rowcount == 1


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


async def save_corpus_message(guild_id: int, channel_id: int, content: str, message_id: int | None = None) -> bool:
    text = (content or "").strip()
    if not text:
        return False

    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO corpus_messages (guild_id, channel_id, message_id, content) VALUES (?, ?, ?, ?)",
            (guild_id, channel_id, message_id, text),
        )
        inserted = _was_inserted(cursor)
        await db.commit()
    return inserted


async def save_corpus_and_user_message(
    guild_id: int,
    channel_id: int,
    author_id: int,
    author_name: str,
    content: str,
    message_id: int | None = None,
) -> tuple[bool, bool]:
    text = (content or "").strip()
    if not text:
        return False, False

    db = await get_db()
    async with _db_lock:
        cur1 = await db.execute(
            "INSERT OR IGNORE INTO corpus_messages (guild_id, channel_id, message_id, content) VALUES (?, ?, ?, ?)",
            (guild_id, channel_id, message_id, text),
        )
        corpus_inserted = _was_inserted(cur1)
        cur2 = await db.execute(
            "INSERT OR IGNORE INTO user_corpus (guild_id, author_id, author_name, message_id, content) VALUES (?, ?, ?, ?, ?)",
            (guild_id, author_id, author_name, message_id, text),
        )
        user_inserted = _was_inserted(cur2)
        await db.commit()
    return corpus_inserted, user_inserted


async def count_corpus_messages(guild_id: int, channel_id: int) -> int:
    db = await get_db()
    async with db.execute(
        "SELECT COUNT(*) FROM corpus_messages WHERE guild_id=? AND channel_id=?",
        (guild_id, channel_id),
    ) as cursor:
        row = await cursor.fetchone()
    return int(row[0] if row else 0)


async def get_corpus_messages(guild_id: int, limit: int | None = None) -> list[str]:
    db = await get_db()
    if limit is None:
        query = "SELECT content FROM corpus_messages WHERE guild_id=? ORDER BY RANDOM()"
        params = (guild_id,)
    else:
        query = (
            "SELECT content FROM corpus_messages "
            "WHERE guild_id = ? AND id IN ("
            "    SELECT id FROM corpus_messages WHERE guild_id = ? ORDER BY RANDOM() LIMIT ?"
            ")"
        )
        params = (guild_id, guild_id, limit)
    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def get_corpus_messages_filtered(
    guild_id: int,
    min_words: int = 5,
    limit: int = 300,
) -> list[str]:
    db = await get_db()
    query = (
        "SELECT content FROM corpus_messages "
        "WHERE guild_id = ? "
        "AND (length(content) - length(replace(content, ' ', ''))) >= ? "
        "AND id IN ("
        "    SELECT id FROM corpus_messages "
        "    WHERE guild_id = ? "
        "    AND (length(content) - length(replace(content, ' ', ''))) >= ? "
        "    ORDER BY RANDOM() LIMIT ?"
        ")"
    )
    async with db.execute(query, (guild_id, min_words - 1, guild_id, min_words - 1, limit)) as cursor:
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
        inserted = _was_inserted(cursor)
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
    mention_role_id: int | None = None,
) -> bool:
    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO youtube_subscriptions "
            "(guild_id, channel_id, youtube_channel_id, youtube_channel_name, discord_channel_id, mention_role_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, youtube_channel_id, youtube_channel_name, discord_channel_id, mention_role_id),
        )
        inserted = _was_inserted(cursor)
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
        "SELECT id, guild_id, channel_id, youtube_channel_id, youtube_channel_name, last_video_id, discord_channel_id, mention_role_id "
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
            "mention_role_id": r[7],
        }
        for r in rows
    ]


async def get_all_youtube_subs() -> list[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT id, guild_id, channel_id, youtube_channel_id, youtube_channel_name, last_video_id, discord_channel_id, mention_role_id "
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
            "mention_role_id": r[7],
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


async def set_youtube_mention_role(guild_id: int, youtube_channel_id: str, role_id: int | None) -> bool:
    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "UPDATE youtube_subscriptions SET mention_role_id=? WHERE guild_id=? AND youtube_channel_id=?",
            (role_id, guild_id, youtube_channel_id),
        )
        updated = cursor.rowcount > 0
        await db.commit()
    return updated


async def save_user_message(guild_id: int, author_id: int, author_name: str, content: str, message_id: int | None = None) -> bool:
    text = (content or "").strip()
    if not text:
        return False

    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO user_corpus (guild_id, author_id, author_name, message_id, content) VALUES (?, ?, ?, ?, ?)",
            (guild_id, author_id, author_name, message_id, text),
        )
        inserted = _was_inserted(cursor)
        await db.commit()
    return inserted


async def get_user_messages(guild_id: int, author_id: int, limit: int | None = None) -> list[str]:
    db = await get_db()
    if limit is None:
        query = "SELECT content FROM user_corpus WHERE guild_id=? AND author_id=? ORDER BY RANDOM()"
        params = (guild_id, author_id)
    else:
        query = (
            "SELECT content FROM user_corpus "
            "WHERE guild_id = ? AND author_id = ? AND id IN ("
            "    SELECT id FROM user_corpus WHERE guild_id = ? AND author_id = ? ORDER BY RANDOM() LIMIT ?"
            ")"
        )
        params = (guild_id, author_id, guild_id, author_id, limit)
    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def count_user_messages(guild_id: int, author_id: int) -> int:
    db = await get_db()
    async with db.execute(
        "SELECT COUNT(*) FROM user_corpus WHERE guild_id=? AND author_id=?",
        (guild_id, author_id),
    ) as cursor:
        row = await cursor.fetchone()
    return int(row[0] if row else 0)


async def add_ignored_channel(guild_id: int, channel_id: int) -> bool:
    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO ignored_channels (guild_id, channel_id) VALUES (?, ?)",
            (guild_id, channel_id),
        )
        inserted = _was_inserted(cursor)
        await db.commit()
    return inserted


async def remove_ignored_channel(guild_id: int, channel_id: int) -> bool:
    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "DELETE FROM ignored_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        )
        removed = cursor.rowcount > 0
        await db.commit()
    return removed


async def list_ignored_channels(guild_id: int) -> list[int]:
    db = await get_db()
    async with db.execute(
        "SELECT channel_id FROM ignored_channels WHERE guild_id=? ORDER BY channel_id",
        (guild_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def is_channel_ignored(guild_id: int, channel_id: int) -> bool:
    db = await get_db()
    async with db.execute(
        "SELECT 1 FROM ignored_channels WHERE guild_id=? AND channel_id=? LIMIT 1",
        (guild_id, channel_id),
    ) as cursor:
        row = await cursor.fetchone()
    return row is not None


async def add_meme_schedule(guild_id: int, channel_id: int, interval_minutes: int) -> bool:
    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "INSERT OR REPLACE INTO meme_schedule (guild_id, channel_id, interval_minutes) VALUES (?, ?, ?)",
            (guild_id, channel_id, interval_minutes),
        )
        inserted = cursor.rowcount > 0
        await db.commit()
    return inserted


async def remove_meme_schedule(guild_id: int, channel_id: int) -> bool:
    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "DELETE FROM meme_schedule WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        )
        removed = cursor.rowcount > 0
        await db.commit()
    return removed


async def list_meme_schedules(guild_id: int) -> list[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT channel_id, interval_minutes, last_posted_at FROM meme_schedule WHERE guild_id=? ORDER BY channel_id",
        (guild_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {"channel_id": r[0], "interval_minutes": r[1], "last_posted_at": r[2]}
        for r in rows
    ]


async def get_due_meme_schedules() -> list[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT guild_id, channel_id, interval_minutes FROM meme_schedule "
        "WHERE last_posted_at IS NULL "
        "   OR datetime(last_posted_at, '+' || interval_minutes || ' minutes') <= datetime('now')"
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {"guild_id": r[0], "channel_id": r[1], "interval_minutes": r[2]}
        for r in rows
    ]


async def update_meme_last_posted(guild_id: int, channel_id: int) -> None:
    db = await get_db()
    async with _db_lock:
        await db.execute(
            "UPDATE meme_schedule SET last_posted_at = datetime('now') WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        )
        await db.commit()


async def save_image_url(guild_id: int, url: str) -> bool:
    """Guarda URL de imagen del server. Retorna True si fue insertada."""
    u = (url or "").strip()
    if not u:
        return False
    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO corpus_images (guild_id, url) VALUES (?, ?)",
            (guild_id, u),
        )
        inserted = _was_inserted(cursor)
        await db.commit()
    return inserted


async def get_random_image_url(guild_id: int) -> str | None:
    """Retorna una URL de imagen random del pool del server."""
    db = await get_db()
    async with db.execute(
        "SELECT url FROM corpus_images WHERE guild_id=? ORDER BY RANDOM() LIMIT 1",
        (guild_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None


async def count_image_urls(guild_id: int) -> int:
    db = await get_db()
    async with db.execute(
        "SELECT COUNT(*) FROM corpus_images WHERE guild_id=?",
        (guild_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return int(row[0] if row else 0)


async def get_random_image_url_excluding(
    guild_id: int,
    exclude_url: str | None = None,
) -> str | None:
    db = await get_db()
    if exclude_url:
        async with db.execute(
            "SELECT url FROM corpus_images "
            "WHERE guild_id=? AND url != ? "
            "ORDER BY RANDOM() LIMIT 1",
            (guild_id, exclude_url),
        ) as cursor:
            row = await cursor.fetchone()
    else:
        async with db.execute(
            "SELECT url FROM corpus_images "
            "WHERE guild_id=? ORDER BY RANDOM() LIMIT 1",
            (guild_id,),
        ) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else None
