"""Núcleo de Purgito: limpieza de corpus, modelos Markov y generación de respuestas.

Este módulo concentra la "personalidad" del bot (post-proceso, frases especiales,
cadencia de generación). Cualquier cambio aquí afecta el tono en producción.
"""

import asyncio
import logging
import random
import re
import time

import regex

import config
from db import (
    get_corpus_messages,
    get_random_frase_especial,
    get_user_messages,
    trim_corpus_if_needed,
    trim_user_corpus_if_needed,
)
from markov_engine import SimpleMarkov
from utils import LRUDict

log = logging.getLogger(__name__)

# Conectores comunes para recortar finales de frases incompletas.
_CONECTORES_FINALES = [
    " y", " o", " con", " pero", " de", " para", " a", " que", " entonces", " como"
]

_markov_cache: LRUDict = LRUDict(64)
_message_counter: LRUDict = LRUDict(256)
_corpus_insert_counter: LRUDict = LRUDict(256)
_user_markov_cache: LRUDict = LRUDict(64)
_user_corpus_insert_counter: LRUDict = LRUDict(256)
_special_phrase_cooldowns: LRUDict = LRUDict(256)

_EMOJI_RE = regex.compile(r'[\p{Extended_Pictographic}\p{Emoji_Component}]+', regex.UNICODE)

_ANSI_ESCAPE_RE = re.compile(r'(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]')
_ANSI_BRACKET_RE = re.compile(r'\]\d*;[^\]]*')
_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)
_DISCORD_MENTIONS_RE = re.compile(r'<a?:\w+:\d+>|<@!?\d+>|<#\d+>|<@&\d+>')


# Postproceso para recortar muletillas y finales raros.
def post_process_reply(text: str) -> str:
    if not text:
        return "no pude generar una respuesta, intenta de nuevo"

    # Limpieza básica
    text = text.lower().strip()
    text = _EMOJI_RE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text)

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

    if not text.strip():
        text = "no tengo respuesta para eso"

    return text.strip()


def clean_for_corpus(text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return None

    # Eliminar secuencias ANSI y basura típica de logs
    t = _ANSI_ESCAPE_RE.sub(" ", t)
    t = _ANSI_BRACKET_RE.sub(" ", t)
    t = t.replace("[0m", " ").replace("][", " ")

    # Eliminar URLs y menciones Discord
    t = _URL_RE.sub(" ", t)
    t = _DISCORD_MENTIONS_RE.sub(" ", t)
    t = re.sub(r'\b\d{5,}\b', '', t)

    # Eliminar líneas que sean solo números/símbolos/caracteres especiales
    kept_lines: list[str] = []
    for line in t.splitlines():
        s = line.strip()
        if not s:
            continue
        if not any(ch.isalpha() for ch in s):
            continue
        kept_lines.append(s)

    t = " ".join(kept_lines)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    return t


def note_corpus_insert(guild_id: int, channel_id: int) -> None:
    key = (guild_id, channel_id)
    n = _corpus_insert_counter.get(key, 0) + 1
    if n >= 50:
        _corpus_insert_counter[key] = 0
        _markov_cache.pop(guild_id, None)
        asyncio.create_task(trim_corpus_if_needed(guild_id))
    else:
        _corpus_insert_counter[key] = n


def note_user_corpus_insert(guild_id: int, author_id: int) -> None:
    key = (guild_id, author_id)
    n = _user_corpus_insert_counter.get(key, 0) + 1
    if n >= 50:
        _user_corpus_insert_counter[key] = 0
        _user_markov_cache.pop(key, None)
        asyncio.create_task(trim_user_corpus_if_needed(guild_id))
    else:
        _user_corpus_insert_counter[key] = n


def note_message_for_auto_generate(guild_id: int, channel_id: int) -> bool:
    """Cuenta un insert al corpus del canal y decide si el bot habla espontáneamente.

    Cada AUTO_GENERATE_EVERY inserts hay una oportunidad de generación, gateada
    por AUTO_GENERATE_PROBABILITY para que no sea determinística por conteo.
    """
    key = (guild_id, channel_id)
    n = _message_counter.get(key, 0) + 1
    if n >= config.AUTO_GENERATE_EVERY:
        _message_counter[key] = 0
        return random.random() < config.AUTO_GENERATE_PROBABILITY
    _message_counter[key] = n
    return False


def reset_guild_caches(guild_id: int) -> None:
    """Limpia todos los caches en memoria de un guild (tras corpus_wipe)."""
    _markov_cache.pop(guild_id, None)
    for cache in (_corpus_insert_counter, _message_counter, _user_corpus_insert_counter, _user_markov_cache):
        for key in [k for k in cache.keys() if k[0] == guild_id]:
            cache.pop(key, None)


async def build_markov_model(guild_id: int) -> SimpleMarkov | None:
    cached = _markov_cache.get(guild_id)
    if cached is not None:
        return cached

    corpus = await get_corpus_messages(guild_id, limit=config.MARKOV_TRAINING_MESSAGES)
    if len(corpus) < 50:
        return None

    def build() -> SimpleMarkov:
        m = SimpleMarkov()
        m.add_many(corpus)
        return m

    try:
        model = await asyncio.to_thread(build)
    except Exception:
        log.exception("Error construyendo modelo Markov para guild %s", guild_id)
        return None

    _markov_cache[guild_id] = model
    return model


async def generate_markov_reply(guild_id: int) -> str | None:
    model = await build_markov_model(guild_id)
    if not model or model.is_empty:
        return None

    try:
        sentence = await asyncio.to_thread(
            model.generate,
            max_words=20,
            max_attempts=5,
            min_words=1,
        )
    except Exception:
        log.exception("Error generando frase Markov para guild %s", guild_id)
        sentence = None

    return sentence


async def generate_markov_for_user(guild_id: int, author_id: int) -> str | None:
    key = (guild_id, author_id)
    model = _user_markov_cache.get(key)
    if model is None:
        corpus = await get_user_messages(guild_id, author_id, limit=config.USER_MARKOV_TRAINING_MESSAGES)
        if len(corpus) < 30:
            return None

        def build() -> SimpleMarkov:
            m = SimpleMarkov()
            m.add_many(corpus)
            return m

        try:
            model = await asyncio.to_thread(build)
        except Exception:
            log.exception("Error construyendo modelo Markov para usuario %s", author_id)
            return None
        _user_markov_cache[key] = model

    try:
        sentence = await asyncio.to_thread(
            model.generate,
            max_words=20,
            max_attempts=5,
            min_words=1,
        )
    except Exception:
        log.exception("Error generando frase Markov para usuario %s", author_id)
        sentence = None
    return sentence


async def generate_response(guild_id: int) -> tuple[str | None, bool]:
    """Decide entre frase especial o Markov. Retorna (texto, es_especial).
    es_especial=True indica que el texto no debe pasar por post_process_reply."""
    now = time.monotonic()
    cooldown_ok = now - _special_phrase_cooldowns.get(guild_id, 0.0) >= config.SPECIAL_PHRASE_COOLDOWN
    if cooldown_ok and random.random() < config.SPECIAL_PHRASE_PROBABILITY:
        phrase = await get_random_frase_especial(guild_id)
        if phrase:
            _special_phrase_cooldowns[guild_id] = now
            return phrase, True
    return await generate_markov_reply(guild_id), False
