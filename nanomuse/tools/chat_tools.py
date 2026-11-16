"""Chat tools: things the agent does to the conversation itself.

``react``: the agent's emoji on what the person said. One to three emoji ride the
user's message as small chips beside the bubble (``reactions`` on the user event),
the way a friend reacts in a messaging app. The reaction replaces whatever the agent
had put on that message before, so a second call is an adjustment, not a pile-up.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment

MAX_REACTIONS = 3


def parse_emojis(raw: Any) -> list[str]:
    """The emoji the model meant, as a clean list of at most three.

    The model sends one string (``"🔥"``, ``"🔥 ✨"``, ``"🔥,✨"``) or a list; either way
    the pieces are split, stripped and kept only when they are emoji; anything plain
    ASCII (a word, a digit, punctuation) is dropped, so ``"lol 🔥"`` reacts with 🔥 and
    not with "lol". Multi-codepoint emoji (skin tones, ZWJ families, flags: one flag
    is two regional indicators) survive as one piece, because they carry no spaces.
    """
    if isinstance(raw, str):
        pieces = raw.replace(",", " ").replace("、", " ").split()
    elif isinstance(raw, list | tuple):
        pieces = [str(p) for chunk in raw for p in str(chunk).replace(",", " ").split()]
    else:
        return []
    emoji: list[str] = []
    for piece in pieces:
        if not piece or len(piece) > 12:  # a grapheme run this long is words, not one emoji
            continue
        if all(ord(ch) < 128 for ch in piece):  # plain ASCII is never an emoji
            continue
        if any(unicodedata.category(ch).startswith(("L", "N")) and ord(ch) > 0x2000 for ch in piece):
            continue  # real letters or digits (CJK words, digits in other scripts)
        if piece not in emoji:
            emoji.append(piece)
    return emoji[:MAX_REACTIONS]


class React(BaseTool):
    name: str = "react"
    description: str = (
        "React to what the user just said with one to three emoji, shown as small emoji "
        "chips on their message, like a reaction in a messaging app. Use it when a "
        "message genuinely lands: something funny, exciting, sweet, big news, a great "
        "idea (🔥 ✨ 💜 😂 🎉 🤯 👏 🥹 💡 🙌…). It is a flourish *beside* the reply, never a "
        "substitute for one: always answer normally too, and do not react to every "
        "message: plain questions and mundane replies need none. A later call on the "
        "same message replaces the emoji already there."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "emojis": {
                "type": "string",
                "description": "One to three emoji, separated by spaces (e.g. '🔥 ✨')",
            },
        },
        "required": ["emojis"],
    }
    risk: RiskLevel = RiskLevel.SAFE
    ui: Any = None  # UI protocol instance

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        a = super().assess(args)
        a.summary = f"react: {str(args.get('emojis', ''))[:24]}"
        return a

    async def execute(self, emojis: str = "", message: str = "", **_: Any) -> ToolResult:
        picked = parse_emojis(emojis)
        if not picked:
            return ToolResult.fail(
                "no emoji in that; pass one to three emoji, like '🔥' or '🔥 ✨ 💜'"
            )
        if self.ui is None:
            return ToolResult.fail("no chat to react in")
        ui: Any = self.ui
        thread = ui.thread()
        timeline = ui.get_timeline(thread)
        target = None
        if message:
            ev = timeline.get(str(message))
            if ev is not None and ev.get("type") == "user":
                target = ev
        else:
            for ev in reversed(timeline.events):
                if ev.get("type") == "user":
                    target = ev
                    break
        if target is None:
            return ToolResult.fail(
                "no message of the user's to react to"
                + (f" (event {message} is not one of their messages)" if message else "")
            )
        ui.patch(thread, str(target["id"]), reactions=picked)
        return ToolResult(output="Reacted to their message: " + " ".join(picked))


__all__ = ["React", "parse_emojis"]
