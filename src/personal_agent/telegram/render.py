"""Split replies into Telegram messages."""

from __future__ import annotations

import re

# Telegram allows 4,096 UTF-16 code units per message; the margin covers any
# difference in how Telegram normalises text.
MAX_CHUNK_UNITS = 4_000
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def split_reply(text: str, limit: int = MAX_CHUNK_UNITS) -> list[str]:
    """Split at paragraph breaks, then sentence ends, then spaces; never mid-word
    unless a single word exceeds the limit."""
    text = text.strip()
    if not text:
        return []
    if utf16_length(text) <= limit:
        return [text]
    chunks: list[str] = []
    for piece in _pieces(text, limit):
        if chunks and utf16_length(f"{chunks[-1]}{piece.separator}{piece.text}") <= limit:
            chunks[-1] = f"{chunks[-1]}{piece.separator}{piece.text}"
        else:
            chunks.append(piece.text)
    return chunks


class _Piece:
    __slots__ = ("separator", "text")

    def __init__(self, text: str, separator: str) -> None:
        self.text = text
        self.separator = separator


def _pieces(text: str, limit: int) -> list[_Piece]:
    """Units no longer than the limit, each with the separator that joins it to the previous."""
    pieces: list[_Piece] = []
    for paragraph in (part.strip() for part in re.split(r"\n\s*\n", text)):
        if not paragraph:
            continue
        separator = "\n\n"
        if utf16_length(paragraph) <= limit:
            pieces.append(_Piece(paragraph, separator))
            continue
        for sentence in (part for part in _SENTENCE_END.split(paragraph) if part):
            if utf16_length(sentence) <= limit:
                pieces.append(_Piece(sentence, separator))
            else:
                for word_run in _hard_split(sentence, limit):
                    pieces.append(_Piece(word_run, separator))
                    separator = " "
            separator = " "
    return pieces


def _hard_split(text: str, limit: int) -> list[str]:
    runs: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if utf16_length(candidate) <= limit:
            current = candidate
            continue
        if current:
            runs.append(current)
        while utf16_length(word) > limit:
            cut = _cut_index(word, limit)
            runs.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        runs.append(current)
    return runs


def _cut_index(word: str, limit: int) -> int:
    units = 0
    for index, character in enumerate(word):
        units += utf16_length(character)
        if units > limit:
            return index
    return len(word)
