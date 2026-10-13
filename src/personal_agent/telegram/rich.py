"""Turn the model's Telegram Rich Markdown into what is sent.

The model writes Telegram Rich Markdown plus the agent's own `pa-` directives
(reaction, effect, poll). Rendering validates and removes the directives, keeps
only quick-reply, URL, and copy-text buttons (quick replies get stored tokens in
place of the model's callback data), and removes media whose URL the owner never
shared. Nothing here calls Telegram.
"""

from __future__ import annotations

import html
import re
import secrets
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from personal_agent.telegram.render import split_reply

# Telegram allows 32,768 characters per rich message; the margin covers how
# Telegram counts formula sources and emoji alternative text.
RICH_MAX_CHARS = 30_000
MAX_QUICK_REPLIES = 12
MAX_LABEL_CHARS = 64
MAX_POLL_OPTIONS = 12
MAX_POLL_QUESTION_CHARS = 300
MAX_POLL_OPTION_CHARS = 100
# Message effects by name. Telegram does not publish effect IDs for bots; these are
# the commonly used ones, verified live. A send rejected for its effect is retried
# without it.
EFFECTS = {
    "celebrate": "5046509860389126442",
    "love": "5159385139981059251",
    "like": "5107584321108051014",
    "fire": "5104841245755180586",
    "dislike": "5104858069142078462",
    "poop": "5046589136895476101",
}
# The emoji Telegram accepts in setMessageReaction (Bot API ReactionTypeEmoji).
REACTIONS = frozenset(
    [
        "❤",
        "👍",
        "👎",
        "🔥",
        "🥰",
        "👏",
        "😁",
        "🤔",
        "🤯",
        "😱",
        "🤬",
        "😢",
        "🎉",
        "🤩",
        "🤮",
        "💩",
        "🙏",
        "👌",
        "🕊",
        "🤡",
        "🥱",
        "🥴",
        "😍",
        "🐳",
        "❤\u200d🔥",
        "🌚",
        "🌭",
        "💯",
        "🤣",
        "⚡",
        "🍌",
        "🏆",
        "💔",
        "🤨",
        "😐",
        "🍓",
        "🍾",
        "💋",
        "🖕",
        "😈",
        "😴",
        "😭",
        "🤓",
        "👻",
        "👨\u200d💻",
        "👀",
        "🎃",
        "🙈",
        "😇",
        "😨",
        "🤝",
        "✍",
        "🤗",
        "🫡",
        "🎅",
        "🎄",
        "☃",
        "💅",
        "🤪",
        "🗿",
        "🆒",
        "💘",
        "🙉",
        "🦄",
        "😘",
        "💊",
        "🙊",
        "😎",
        "👾",
        "🤷\u200d♂",
        "🤷",
        "🤷\u200d♀",
        "😡",
    ]
)
_VARIATION_SELECTOR = "️"
_BUTTON_STYLES = frozenset({"danger", "success", "primary", "link"})

_REACT = re.compile(r"<pa-react\b([^>]*?)/?>(?:\s*</pa-react>)?")
_EFFECT = re.compile(r"<pa-effect\b([^>]*?)/?>(?:\s*</pa-effect>)?")
_POLL = re.compile(r"<pa-poll\b([^>]*)>(.*?)</pa-poll>", re.DOTALL)
_POLL_OPTION = re.compile(r"<option>(.*?)</option>", re.DOTALL)
_LEFTOVER_DIRECTIVE = re.compile(r"</?pa-[a-z-]*\b[^>]*>")
# The lookahead keeps <tg-button-row> from matching as a button.
_BUTTON = re.compile(r"<tg-button(?=[\s>])([^>]*)>(.*?)</tg-button>", re.DOTALL)
_KEPT = re.compile("\x00(\\d+)\x00")
_EMPTY_ROW = re.compile(r"<tg-button-row\b[^>]*>\s*</tg-button-row>")
_ATTRIBUTE = re.compile(r'([A-Za-z][\w-]*)(?:\s*=\s*"([^"]*)")?')
_MARKDOWN_MEDIA = re.compile(r'!\[([^\]]*)\]\((\S+?)(?:\s+"([^"]*)")?\)')
_HTML_MEDIA = re.compile(r"<(?:img|video|audio)\b[^>]*?\bsrc=\"([^\"]*)\"[^>]*?/?>")
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class QuickReply:
    token: str
    label: str
    position: int


@dataclass(frozen=True, slots=True)
class PollSpec:
    question: str
    options: list[str]
    multiple: bool


@dataclass(frozen=True, slots=True)
class RenderedReply:
    # What the assistant said, as stored: valid directives kept, invalid parts removed.
    stored: str
    # The rich messages to send, in order; empty when the reply is only a reaction.
    chunks: list[str]
    reaction: str | None
    effect: str | None
    poll: PollSpec | None
    quick_replies: list[QuickReply]
    # Counts of removed parts by reason, for content-free logs.
    dropped: dict[str, int] = field(default_factory=dict)

    @property
    def sends_anything(self) -> bool:
        return bool(self.chunks or self.reaction or self.poll)


def new_button_token() -> str:
    return f"b:{secrets.token_hex(8)}"


def _attributes(raw: str) -> dict[str, str]:
    return {name: html.unescape(value or "") for name, value in _ATTRIBUTE.findall(raw)}


def _plain_label(inner: str) -> str:
    return html.unescape(_TAG.sub("", inner)).strip()


def render_reply(
    text: str,
    *,
    shared_urls: set[str],
    new_token: Callable[[], str] = new_button_token,
) -> RenderedReply:
    dropped: Counter[str] = Counter()
    reaction, effect, poll = None, None, None
    # Valid directives are parked behind placeholders while unknown ones are removed.
    kept: list[str] = []

    def keep(directive: str) -> str:
        kept.append(directive)
        return f"\x00{len(kept) - 1}\x00"

    def take_reaction(match: re.Match[str]) -> str:
        nonlocal reaction
        emoji = _attributes(match.group(1)).get("emoji", "").replace(_VARIATION_SELECTOR, "")
        if reaction is None and emoji in REACTIONS:
            reaction = emoji
            return keep(f'<pa-react emoji="{emoji}"/>')
        dropped["reaction"] += 1
        return ""

    def take_effect(match: re.Match[str]) -> str:
        nonlocal effect
        name = _attributes(match.group(1)).get("name", "")
        if effect is None and name in EFFECTS:
            effect = name
            return keep(f'<pa-effect name="{name}"/>')
        dropped["effect"] += 1
        return ""

    def take_poll(match: re.Match[str]) -> str:
        nonlocal poll
        attributes = _attributes(match.group(1))
        question = attributes.get("question", "").strip()
        options = [_plain_label(option) for option in _POLL_OPTION.findall(match.group(2))]
        valid = (
            poll is None
            and 0 < len(question) <= MAX_POLL_QUESTION_CHARS
            and 0 < len(options) <= MAX_POLL_OPTIONS
            and all(0 < len(option) <= MAX_POLL_OPTION_CHARS for option in options)
        )
        if not valid:
            dropped["poll"] += 1
            return ""
        poll = PollSpec(question, options, attributes.get("multiple", "false") == "true")
        return keep(match.group(0))

    stored = _REACT.sub(take_reaction, text)
    stored = _EFFECT.sub(take_effect, stored)
    stored = _POLL.sub(take_poll, stored)
    leftovers = _LEFTOVER_DIRECTIVE.findall(stored)
    if leftovers:
        dropped["directive"] += len(leftovers)
        stored = _LEFTOVER_DIRECTIVE.sub("", stored)
    stored = _KEPT.sub(lambda match: kept[int(match.group(1))], stored)

    quick_replies: list[QuickReply] = []

    def take_button(match: re.Match[str]) -> str:
        attributes = _attributes(match.group(1))
        kind = attributes.get("type", "")
        style = attributes.get("style", "")
        inner = match.group(2).strip()
        label = _plain_label(inner)[:MAX_LABEL_CHARS]
        if not label:
            dropped["button"] += 1
            return ""
        if kind == "callback_data" and len(quick_replies) < MAX_QUICK_REPLIES:
            token = new_token()
            quick_replies.append(QuickReply(token, label, len(quick_replies)))
            style_attribute = f' style="{style}"' if style in _BUTTON_STYLES else ""
            return f'<tg-button type="callback_data" data="{token}"{style_attribute}>{inner}</tg-button>'
        style_attribute = f' style="{style}"' if style in _BUTTON_STYLES - {"link"} else ""
        url = attributes.get("url", "")
        if kind == "url" and url.startswith(("https://", "http://")):
            return f'<tg-button type="url" url="{html.escape(url)}"{style_attribute}>{inner}</tg-button>'
        copy_text = attributes.get("text", "")
        if kind == "copy_text" and 0 < len(copy_text) <= 256:
            return f'<tg-button type="copy_text" text="{html.escape(copy_text)}"{style_attribute}>{inner}</tg-button>'
        dropped["button"] += 1
        return ""

    stored = _BUTTON.sub(take_button, stored)
    stored = _EMPTY_ROW.sub("", stored)

    def keep_shared_markdown_media(match: re.Match[str]) -> str:
        if _is_shared(match.group(2), shared_urls):
            return match.group(0)
        dropped["media"] += 1
        return ""

    def keep_shared_html_media(match: re.Match[str]) -> str:
        if _is_shared(html.unescape(match.group(1)), shared_urls):
            return match.group(0)
        dropped["media"] += 1
        return ""

    stored = _MARKDOWN_MEDIA.sub(keep_shared_markdown_media, stored)
    stored = _HTML_MEDIA.sub(keep_shared_html_media, stored).strip()
    markdown = strip_directives(stored)
    chunks = split_reply(markdown, limit=RICH_MAX_CHARS) if markdown else []
    return RenderedReply(
        stored=stored,
        chunks=chunks,
        reaction=reaction,
        effect=effect,
        poll=poll,
        quick_replies=quick_replies,
        dropped=dict(dropped),
    )


def _is_shared(url: str, shared_urls: set[str]) -> bool:
    # Telegram's own links (time stamps, custom emoji) are not fetched media.
    if not url.startswith(("https://", "http://")):
        return True
    return url in shared_urls


def _strip_complete_directives(text: str) -> str:
    return _POLL.sub("", _EFFECT.sub("", _REACT.sub("", text)))


def strip_directives(text: str) -> str:
    return _LEFTOVER_DIRECTIVE.sub("", _strip_complete_directives(text)).strip()


def draft_view(partial: str) -> str:
    """What a streaming draft may show: no directives, buttons, or unfinished markup."""
    text = _strip_complete_directives(partial)
    for marker in ("<pa-", "<tg-button"):
        index = text.find(marker)
        if index != -1:
            text = text[:index]
    last_open = text.rfind("<")
    if last_open > text.rfind(">"):
        text = text[:last_open]
    if text.count("```") % 2 == 1:
        text += "\n```"
    if text.count("$$") % 2 == 1:
        text = text[: text.rfind("$$")]
    return text.strip()


def mark_chosen(markdown: str, label: str) -> str:
    """The sent message with its quick replies replaced by the chosen option."""
    without = re.sub(
        r'<tg-button type="callback_data"[^>]*>.*?</tg-button>', "", markdown, flags=re.DOTALL
    )
    without = _EMPTY_ROW.sub("", without).rstrip()
    return f"{without}\n\n✓ {label}".strip()


def to_plain(markdown: str) -> str:
    """Readable plain text for when Telegram rejects the rich message."""
    text = re.sub(r"<tg-button-row\b[^>]*>.*?</tg-button-row>", "", markdown, flags=re.DOTALL)
    text = _BUTTON.sub("", text)
    text = re.sub(r"<summary>(.*?)</summary>", r"\1\n", text, flags=re.DOTALL)
    text = _MARKDOWN_MEDIA.sub(
        lambda match: f"{match.group(3) or match.group(1)} {match.group(2)}".strip(), text
    )
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", text)
    text = _TAG.sub("", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```.*$", "", text, flags=re.MULTILINE)
    for marker in ("**", "__", "~~", "==", "||", "$$"):
        text = text.replace(marker, "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return html.unescape(text).strip()
