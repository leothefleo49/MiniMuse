"""Pack request copies without changing saved history or splitting tool exchanges."""

from __future__ import annotations

from typing import Any

from nanomuse.llm.budget import BudgetExceeded, estimate_tokens
from nanomuse.logger import logger
from nanomuse.schema import Message, Role


def fit_context(
    messages: list[Message],
    tools: list[dict[str, Any]] | None,
    limit: int,
    protected: list[Message],
) -> list[Message]:
    if not limit:
        return messages
    groups: list[list[Message]] = []
    for message in messages:
        if message.role == Role.TOOL and groups and groups[-1][0].tool_calls:
            groups[-1].append(message)
        else:
            groups.append([message])
    pinned = {m.meta.get("request_id", id(m)) for m in protected}

    # Tool output is untrusted data, not an instruction. Keep a labelled head and tail.
    # Never truncate system/user text or function arguments to make a request fit.
    def flatten() -> list[Message]:
        return [m for group in groups for m in group]

    def shorten(message: Message, output_chars: int) -> Message:
        content = message.content or ""
        if message.role != Role.TOOL or len(content) <= output_chars:
            return message
        shortened = (
            content[: output_chars // 2]
            + "\n[Tool output shortened for context budget]\n"
            + content[-output_chars // 2 :]
        )
        logger.info(
            "Context budget shortened tool result {}: {} -> {} characters",
            message.tool_call_id or "unknown",
            len(content),
            len(shortened),
        )
        return message.model_copy(update={"content": shortened})

    for output_chars in (4096, 1024, 256):
        if estimate_tokens(flatten(), tools) <= limit:
            # Nothing has to be dropped: the request already fits. Return here directly
            # (the ledger below only exists for what gets dropped), which is why this
            # path must not fall through to the `packed` name defined further down.
            return flatten()
        for i, group in enumerate(groups):
            groups[i] = [shorten(m, output_chars) for m in group]
    logger.info(
        "Context budget tightened to {} of {} with {} groups left; tool schemas ~{}",
        estimate_tokens(flatten(), tools),
        limit,
        len(groups),
        estimate_tokens([], tools) if tools else 0,
    )
    # The user's own words are the spine of a long build session. When the budget forces
    # old turns out, their requests stay behind as a compact ledger, so a later "what
    # about all the previous stuff I asked you to do?" is still answerable from the
    # request itself. Assistant prose and tool output go first; the asks are kept.
    ledger: list[str] = []
    ledger_budget = max(600, min(4000, limit // 12))

    def fold(group: list[Message]) -> None:
        """Keep the asks from a group that is about to be dropped."""
        for message in group:
            if message.role != Role.USER:
                continue
            text = " ".join((message.content or "").split())
            if not text:
                continue
            entry = text if len(text) <= 240 else text[:237] + "..."
            if entry not in ledger:
                ledger.append(entry)
        while ledger and estimate_tokens(
            [Message(role=Role.SYSTEM, content=ledger_block())], None
        ) > ledger_budget:
            ledger.pop(0)

    def ledger_block() -> str:
        return (
            "Earlier in this conversation the user asked for, in their own words "
            "(oldest first, kept because the full messages no longer fit):\n"
            + "\n".join(f"- {line}" for line in ledger)
        )

    while estimate_tokens(flatten(), tools) > limit:
        removable = next(
            (
                i
                for i, group in enumerate(groups[:-1])
                if group[0].role != Role.SYSTEM
                and not any(m.meta.get("request_id", id(m)) in pinned for m in group)
            ),
            None,
        )
        if removable is not None:
            fold(groups[removable])
            del groups[removable]
            continue
        # Only pinned and system groups are left: old pictures go next, oldest first. The
        # words around a picture carry what happened to it, and the newest exchange keeps
        # its own images, so a turn never loses the picture it is being asked about.
        victim = next(
            (
                i
                for i, group in enumerate(groups[:-1])
                if any(m.images for m in group)
                and not any(m.meta.get("request_id", id(m)) in pinned for m in group)
            ),
            None,
        )
        if victim is None:
            rows = []
            for g in groups:
                head = (g[0].content or "").strip().replace("\n", " ")[:44]
                rows.append(f"{g[0].role.value}~{estimate_tokens(g)}x{len(g)} «{head}»")
            logger.warning(
                "Context budget cannot fit: {} of {} after every drop; remaining: {}",
                estimate_tokens(flatten(), tools),
                limit,
                " | ".join(rows),
            )
            raise BudgetExceeded("context")
        logger.info(
            "Context budget sent out an old turn's picture ({}), words kept",
            groups[victim][0].tool_call_id or groups[victim][0].content[:24] or "unknown",
        )
        groups[victim] = [
            m.model_copy(update={"images": None}) if m.images else m for m in groups[victim]
        ]
    packed: list[Message] = flatten()
    if ledger:
        at = 0
        for i, message in enumerate(packed):
            if message.role == Role.SYSTEM:
                at = i + 1
            else:
                break
        packed.insert(at, Message(role=Role.SYSTEM, content=ledger_block()))
        logger.info("Context budget kept {} earlier user request(s) as a ledger", len(ledger))
    return packed
