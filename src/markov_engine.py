import random
from collections import defaultdict


class SimpleMarkov:
    """Generador de cadenas de Markov de primer orden estilo nimkov.

    Diferencias clave con markovify:
    - No valida estructura de "sentence"
    - Termina naturalmente cuando la cadena alcanza fin-de-mensaje
    - Las transiciones se pesan por frecuencia natural (lista con repetición)
    """

    START = "__START__"
    END = "__END__"

    def __init__(self):
        self.transitions: dict[str, list[str]] = defaultdict(list)

    def add(self, message: str) -> None:
        words = message.split()
        if not words:
            return
        prev = self.START
        for word in words:
            self.transitions[prev].append(word)
            prev = word
        self.transitions[prev].append(self.END)

    def add_many(self, messages: list[str]) -> None:
        for msg in messages:
            self.add(msg)

    def generate(
        self,
        max_words: int = 30,
        max_attempts: int = 5,
        min_words: int = 1,
    ) -> str | None:
        if not self.transitions:
            return None
        for _ in range(max_attempts):
            words: list[str] = []
            current = self.START
            for _ in range(max_words):
                next_options = self.transitions.get(current)
                if not next_options:
                    break
                next_word = random.choice(next_options)
                if next_word == self.END:
                    break
                words.append(next_word)
                current = next_word
            if len(words) >= min_words:
                return " ".join(words)
        return None

    @property
    def is_empty(self) -> bool:
        return not self.transitions
