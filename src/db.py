import os
import re
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

CREATE INDEX IF NOT EXISTS idx_corpus_messages_guild ON corpus_messages(guild_id);
CREATE INDEX IF NOT EXISTS idx_corpus_messages_guild_channel ON corpus_messages(guild_id, channel_id);

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

CREATE INDEX IF NOT EXISTS idx_user_corpus_guild_author ON user_corpus(guild_id, author_id);

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

CREATE TABLE IF NOT EXISTS frases_especiales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    user_name TEXT NOT NULL,
    frase TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_frases_especiales_guild ON frases_especiales(guild_id);

CREATE TABLE IF NOT EXISTS reaction_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    emoji_text TEXT NOT NULL,
    is_custom INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id, emoji_text)
);
CREATE INDEX IF NOT EXISTS idx_reaction_pool_guild ON reaction_pool(guild_id);
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
    flag_path = os.path.join(DATA_DIR, ".images_wiped_v2")
    if not os.path.exists(flag_path):
        await _db.execute("DELETE FROM corpus_images")
        await _db.commit()
        with open(flag_path, "w") as f:
            f.write("done")
        log.info("corpus_images wipeado - migracion v2")


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


async def list_gif_urls(guild_id: int) -> list[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT id, url, created_at FROM corpus_gifs WHERE guild_id=? ORDER BY id",
        (guild_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [{"id": r[0], "url": r[1], "created_at": r[2]} for r in rows]


async def delete_gif_url_by_id(guild_id: int, gif_id: int) -> bool:
    db = await get_db()
    async with db.execute(
        "SELECT url FROM corpus_gifs WHERE guild_id=? AND id=?",
        (guild_id, gif_id),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return False
    url = row[0]

    async with _db_lock:
        cursor = await db.execute(
            "DELETE FROM corpus_gifs WHERE guild_id=? AND id=?",
            (guild_id, gif_id),
        )
        deleted = cursor.rowcount > 0
        await db.commit()

    if deleted:
        r2_public_url = os.getenv("R2_PUBLIC_URL", "").strip()
        if r2_public_url and url.startswith(r2_public_url):
            try:
                import boto3
                from botocore.config import Config as _BotoConfig
                _ep = os.getenv("R2_ENDPOINT_URL", "").strip()
                _kid = os.getenv("R2_ACCESS_KEY_ID", "").strip()
                _sec = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
                _bkt = os.getenv("R2_BUCKET_NAME", "").strip()
                if _ep and _kid and _sec and _bkt:
                    _key = url[len(r2_public_url.rstrip("/")) + 1:]

                    def _r2_del():
                        c = boto3.client(
                            "s3",
                            endpoint_url=_ep,
                            aws_access_key_id=_kid,
                            aws_secret_access_key=_sec,
                            config=_BotoConfig(signature_version="s3v4"),
                            region_name="auto",
                        )
                        c.delete_object(Bucket=_bkt, Key=_key)

                    await asyncio.to_thread(_r2_del)
            except Exception:
                log.warning("No se pudo eliminar GIF de R2: %s", url)

    return deleted


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


async def delete_image_url(guild_id: int, url: str) -> None:
    db = await get_db()
    async with _db_lock:
        await db.execute(
            "DELETE FROM corpus_images WHERE guild_id=? AND url=?",
            (guild_id, url),
        )
        await db.commit()


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


async def add_frase_especial(guild_id: int, user_id: int, user_name: str, frase: str) -> bool:
    text = (frase or "").strip()
    if not text:
        return False
    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "INSERT INTO frases_especiales (guild_id, user_id, user_name, frase) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, user_name, text),
        )
        inserted = _was_inserted(cursor)
        await db.commit()
    return inserted


async def get_random_frase_especial(guild_id: int) -> str | None:
    db = await get_db()
    async with db.execute(
        "SELECT frase FROM frases_especiales WHERE guild_id=? ORDER BY RANDOM() LIMIT 1",
        (guild_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None


async def list_frases_especiales(guild_id: int) -> list[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT id, user_id, user_name, frase, created_at "
        "FROM frases_especiales WHERE guild_id=? ORDER BY id",
        (guild_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {"id": r[0], "user_id": r[1], "user_name": r[2], "frase": r[3], "created_at": r[4]}
        for r in rows
    ]


async def get_frase_especial(guild_id: int, frase_id: int) -> dict | None:
    db = await get_db()
    async with db.execute(
        "SELECT id, user_id, user_name, frase, created_at "
        "FROM frases_especiales WHERE guild_id=? AND id=?",
        (guild_id, frase_id),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    return {"id": row[0], "user_id": row[1], "user_name": row[2], "frase": row[3], "created_at": row[4]}


async def delete_frase_especial(guild_id: int, frase_id: int) -> bool:
    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "DELETE FROM frases_especiales WHERE guild_id=? AND id=?",
            (guild_id, frase_id),
        )
        deleted = cursor.rowcount > 0
        await db.commit()
    return deleted


_CUSTOM_EMOJI_RE = re.compile(r"^<a?:\w+:\d+>$")


async def add_reaction_to_pool(guild_id: int, emoji_text: str) -> bool:
    text = (emoji_text or "").strip()
    if not text:
        return False
    is_custom = 1 if _CUSTOM_EMOJI_RE.match(text) else 0
    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO reaction_pool (guild_id, emoji_text, is_custom) VALUES (?, ?, ?)",
            (guild_id, text, is_custom),
        )
        inserted = _was_inserted(cursor)
        await db.commit()
    return inserted


async def remove_reaction_from_pool(guild_id: int, reaction_id: int) -> bool:
    db = await get_db()
    async with _db_lock:
        cursor = await db.execute(
            "DELETE FROM reaction_pool WHERE guild_id=? AND id=?",
            (guild_id, reaction_id),
        )
        removed = cursor.rowcount > 0
        await db.commit()
    return removed


async def list_reaction_pool(guild_id: int) -> list[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT id, emoji_text, is_custom FROM reaction_pool WHERE guild_id=? ORDER BY id",
        (guild_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [{"id": r[0], "emoji_text": r[1], "is_custom": bool(r[2])} for r in rows]


async def get_random_reaction(guild_id: int) -> dict | None:
    db = await get_db()
    async with db.execute(
        "SELECT emoji_text FROM reaction_pool WHERE guild_id=? ORDER BY RANDOM() LIMIT 1",
        (guild_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return {"emoji_text": row[0]} if row else None
