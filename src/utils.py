"""Utilidades pequeñas compartidas entre cogs."""

from collections import OrderedDict

import discord


class LRUDict(OrderedDict):
    """Dict con política LRU: al superar maxsize expulsa la entrada menos usada."""

    def __init__(self, maxsize: int):
        super().__init__()
        self._maxsize = maxsize

    def get(self, key, default=None):
        if key not in self:
            return default
        self.move_to_end(key)
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self._maxsize:
            self.popitem(last=False)


def has_admin_permission(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    return interaction.user.guild_permissions.manage_guild


def chunk_message(text: str, max_length: int = 1900) -> list[str]:
    """Divide un texto largo en fragmentos que Discord pueda aceptar, intentando no cortar palabras."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        chunk = text[:max_length]
        last_newline = chunk.rfind('\n')
        last_space = chunk.rfind(' ')
        cut_index = last_newline if last_newline > 0 else (last_space if last_space > 0 else max_length)
        chunks.append(text[:cut_index].strip())
        text = text[cut_index:].strip()
    return chunks
