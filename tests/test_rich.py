from __future__ import annotations

from itertools import count

from personal_agent.telegram.rich import (
    EFFECT_REACTIONS,
    EFFECTS,
    REACTIONS,
    draft_view,
    mark_chosen,
    render_reply,
    to_plain,
)

SHARED = {"https://example.com/cat.jpg"}


def _tokens() -> object:
    numbers = count(1)
    return lambda: f"b:{next(numbers):016x}"


def _render(text: str) -> object:
    return render_reply(text, shared_urls=SHARED, new_token=_tokens())  # pyright: ignore[reportArgumentType]


def test_plain_chat_is_one_rich_chunk_unchanged() -> None:
    rendered = render_reply("Morning! Sleep well?", shared_urls=set())

    assert rendered.chunks == ["Morning! Sleep well?"]
    assert rendered.stored == "Morning! Sleep well?"
    assert (rendered.reaction, rendered.effect, rendered.poll) == (None, None, None)


def test_reaction_alone_sends_no_message() -> None:
    rendered = render_reply('<pa-react emoji="👍"/>', shared_urls=set())

    assert rendered.reaction == "👍"
    assert rendered.chunks == []
    assert rendered.sends_anything
    assert rendered.stored == '<pa-react emoji="👍"/>'


def test_reaction_accepts_the_variation_selector_and_rejects_others() -> None:
    heart = render_reply('<pa-react emoji="❤️"/> Love it', shared_urls=set())
    unknown = render_reply('<pa-react emoji="🦖"/> Rawr', shared_urls=set())

    assert heart.reaction == "❤"
    assert unknown.reaction is None
    assert unknown.dropped == {"reaction": 1}
    assert unknown.chunks == ["Rawr"]


def test_effect_must_be_a_known_name() -> None:
    good = render_reply('<pa-effect name="celebrate"/>Congrats!', shared_urls=set())
    bad = render_reply('<pa-effect name="fireworks"/>Congrats!', shared_urls=set())

    assert good.effect == "celebrate" and "celebrate" in EFFECTS
    assert good.chunks == ["Congrats!"]
    assert bad.effect is None and bad.dropped == {"effect": 1}


def test_an_effect_without_text_becomes_a_reaction_to_the_owner() -> None:
    rendered = render_reply('<pa-effect name="dislike"/>', shared_urls=set())
    both = render_reply('<pa-react emoji="👍"/><pa-effect name="love"/>', shared_urls=set())

    assert (rendered.reaction, rendered.effect, rendered.chunks) == ("👎", None, [])
    assert rendered.stored == '<pa-react emoji="👎"/>'
    assert (both.reaction, both.effect, both.dropped) == ("👍", None, {"effect": 1})
    assert set(EFFECT_REACTIONS) == set(EFFECTS)
    assert set(EFFECT_REACTIONS.values()) <= REACTIONS


def test_poll_is_parsed_and_removed_from_the_message() -> None:
    rendered = render_reply(
        'Here you go.\n<pa-poll question="Friday dinner?" multiple="true">'
        "<option>Pizza</option><option>Sushi</option></pa-poll>",
        shared_urls=set(),
    )

    assert rendered.poll is not None
    assert (rendered.poll.question, rendered.poll.options, rendered.poll.multiple) == (
        "Friday dinner?",
        ["Pizza", "Sushi"],
        True,
    )
    assert rendered.chunks == ["Here you go."]
    assert "<pa-poll" in rendered.stored


def test_text_that_only_repeats_the_poll_question_is_not_sent() -> None:
    rendered = render_reply(
        'Which team?\n<pa-poll question="Which team?"><option>A</option><option>B</option></pa-poll>',
        shared_urls=set(),
    )

    assert rendered.poll is not None
    assert rendered.chunks == []
    assert rendered.sends_anything


def test_invalid_poll_and_unknown_directives_are_dropped() -> None:
    rendered = render_reply(
        '<pa-poll question=""><option>A</option></pa-poll><pa-dance/>Hi', shared_urls=set()
    )

    assert rendered.poll is None
    assert rendered.dropped == {"poll": 1, "directive": 1}
    assert rendered.chunks == ["Hi"]


def test_quick_replies_get_tokens_and_other_buttons_are_filtered() -> None:
    rendered = _render(
        "Which one?\n<tg-button-row>\n"
        '<tg-button type="callback_data" data="en" style="primary">English</tg-button>\n'
        '<tg-button type="callback_data" data="id">Bahasa <b>Indonesia</b></tg-button>\n'
        "</tg-button-row>\n<tg-button-row>\n"
        '<tg-button type="url" url="https://example.com">Docs</tg-button>\n'
        '<tg-button type="url" url="javascript:alert(1)">Bad</tg-button>\n'
        '<tg-button type="copy_text" text="ABC-123">Copy code</tg-button>\n'
        '<tg-button type="web_app" url="https://example.com">App</tg-button>\n'
        "</tg-button-row>\n<tg-button-row>"
        '<tg-button type="login_url" url="https://example.com">Login</tg-button>'
        "</tg-button-row>"
    )

    assert [(reply.token, reply.label) for reply in rendered.quick_replies] == [  # pyright: ignore[reportAttributeAccessIssue]
        ("b:0000000000000001", "English"),
        ("b:0000000000000002", "Bahasa Indonesia"),
    ]
    text = rendered.chunks[0]  # pyright: ignore[reportAttributeAccessIssue]
    assert 'data="b:0000000000000001" style="primary">English' in text
    assert 'data="en"' not in text
    assert 'type="url" url="https://example.com">Docs' in text
    assert 'type="copy_text" text="ABC-123">Copy code' in text
    for removed in ("javascript", "web_app", "login_url"):
        assert removed not in text
    assert text.count("<tg-button-row>") == 2
    assert rendered.dropped == {"button": 3}  # pyright: ignore[reportAttributeAccessIssue]


def test_only_media_the_owner_shared_is_kept() -> None:
    rendered = _render(
        '![](https://example.com/cat.jpg "Your cat")\n'
        "![](https://invented.example/dog.jpg)\n"
        '<img src="https://invented.example/x.png"/>\n'
        "See ![9:00 tomorrow](tg://time?unix=1760400000&format=wt)"
    )

    text = rendered.chunks[0]  # pyright: ignore[reportAttributeAccessIssue]
    assert "https://example.com/cat.jpg" in text
    assert "invented.example" not in text
    assert "tg://time" in text
    assert rendered.dropped == {"media": 2}  # pyright: ignore[reportAttributeAccessIssue]


def test_draft_view_hides_directives_buttons_and_unfinished_markup() -> None:
    assert draft_view('<pa-react emoji="👍"/>Hello there') == "Hello there"
    assert draft_view("Pick one:\n<tg-button-row>\n<tg-button type=") == "Pick one:"
    assert draft_view("Here: <b") == "Here:"
    assert draft_view("```python\nprint(1)") == "```python\nprint(1)\n```"
    assert draft_view('Sure <pa-poll question="x"><option>A') == "Sure"
    assert draft_view("") == ""
    assert draft_view("Facts:\n- **Age:** 13.8 billion years\n- **") == (
        "Facts:\n- **Age:** 13.8 billion years"
    )
    assert draft_view("Steps:\n1. Boil water\n2.") == "Steps:\n1. Boil water"
    assert draft_view("It is **very") == "It is"


def test_mark_chosen_replaces_quick_replies_only() -> None:
    sent = (
        "Which language?\n<tg-button-row>\n"
        '<tg-button type="callback_data" data="b:1">English</tg-button>\n'
        '<tg-button type="callback_data" data="b:2">Indonesian</tg-button>\n'
        "</tg-button-row>\n<tg-button-row>"
        '<tg-button type="url" url="https://example.com">Docs</tg-button></tg-button-row>'
    )

    marked = mark_chosen(sent, "Indonesian")

    assert "callback_data" not in marked
    assert 'type="url"' in marked
    assert marked.endswith("✓ Indonesian")


def test_plain_fallback_is_readable() -> None:
    plain = to_plain(
        "## Plan\n**Bold** and [docs](https://example.com).\n```python\nx = 1\n```\n"
        "<details><summary>More</summary>Hidden</details>\n"
        '<tg-button-row><tg-button type="callback_data" data="b:1">Go</tg-button></tg-button-row>'
    )

    assert plain == "Plan\nBold and docs (https://example.com).\n\nx = 1\n\nMore\nHidden"
