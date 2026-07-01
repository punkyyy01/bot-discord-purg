"""Base de internacionalización de Purgito.

Los strings viven en src/locales/<locale>.json (claves planas con puntos).
Resolución de idioma: configuración del servidor (tabla settings.locale),
con español latino neutro como default. Fallback de claves: locale → es → clave.

Uso:
    from i18n import t, guild_locale
    locale = await guild_locale(guild_id)
    texto = t("settings.title", locale, guild=guild.name)
"""

import json
import logging
import os

from db import get_guild_locale, set_guild_locale
from utils import LRUDict

log = logging.getLogger(__name__)

DEFAULT_LOCALE = "es"
# (código, nombre nativo) — agregar aquí al sumar un idioma nuevo.
SUPPORTED_LOCALES = [
    ("es", "Español"),
    ("en", "English"),
]

_LOCALES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")

_strings: dict[str, dict[str, str]] = {}
_guild_locales: LRUDict = LRUDict(1024)


def _load_locales() -> None:
    for code, _name in SUPPORTED_LOCALES:
        path = os.path.join(_LOCALES_DIR, f"{code}.json")
        try:
            with open(path, encoding="utf-8") as f:
                _strings[code] = json.load(f)
        except Exception:
            log.exception("No se pudo cargar el locale %s (%s)", code, path)
            _strings.setdefault(code, {})


_load_locales()


def t(key: str, locale: str = DEFAULT_LOCALE, **kwargs) -> str:
    """Resuelve un string por clave. Fallback: locale pedido → es → la clave misma."""
    value = _strings.get(locale, {}).get(key)
    if value is None:
        value = _strings.get(DEFAULT_LOCALE, {}).get(key)
    if value is None:
        log.warning("i18n: clave faltante %r (locale=%s)", key, locale)
        return key
    if kwargs:
        try:
            return value.format(**kwargs)
        except Exception:
            log.exception("i18n: error formateando %r", key)
            return value
    return value


def is_supported(locale: str) -> bool:
    return any(code == locale for code, _ in SUPPORTED_LOCALES)


async def guild_locale(guild_id: int | None) -> str:
    """Idioma efectivo de un guild (cacheado). Default: español latino neutro."""
    if guild_id is None:
        return DEFAULT_LOCALE
    cached = _guild_locales.get(guild_id)
    if cached is not None:
        return cached
    locale = await get_guild_locale(guild_id) or DEFAULT_LOCALE
    if not is_supported(locale):
        locale = DEFAULT_LOCALE
    _guild_locales[guild_id] = locale
    return locale


async def set_locale(guild_id: int, locale: str) -> None:
    if not is_supported(locale):
        raise ValueError(f"Locale no soportado: {locale}")
    await set_guild_locale(guild_id, locale)
    _guild_locales[guild_id] = locale
