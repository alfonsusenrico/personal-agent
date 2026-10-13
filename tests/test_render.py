from __future__ import annotations

from personal_agent.telegram.render import MAX_CHUNK_UNITS, split_reply, utf16_length


def test_short_reply_is_one_chunk() -> None:
    assert split_reply("  hi there  ") == ["hi there"]
    assert split_reply("   ") == []


def test_six_thousand_characters_split_at_paragraphs_in_order() -> None:
    paragraphs = [f"Part {index}. " + "x" * 1_500 for index in range(4)]
    reply = "\n\n".join(paragraphs)
    assert len(reply) > 6_000

    chunks = split_reply(reply)

    assert len(chunks) == 2
    assert all(utf16_length(chunk) <= MAX_CHUNK_UNITS for chunk in chunks)
    assert chunks == ["\n\n".join(paragraphs[:2]), "\n\n".join(paragraphs[2:])]


def test_long_paragraph_splits_at_sentence_ends() -> None:
    sentence = "This sentence is a fixed length of words. "
    reply = (sentence * 150).strip()

    chunks = split_reply(reply, limit=1_000)

    assert all(chunk.endswith(".") for chunk in chunks)
    assert " ".join(chunks) == reply


def test_limit_counts_utf16_units() -> None:
    reply = "😀" * 3_000

    chunks = split_reply(reply)

    assert all(utf16_length(chunk) <= MAX_CHUNK_UNITS for chunk in chunks)
    assert "".join(chunks) == reply
