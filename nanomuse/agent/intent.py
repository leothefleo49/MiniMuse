"""Intent classification for nanoMuse agent loop.

Differentiates pure conversational banter, greetings, venting, and stop commands
from actionable requests that genuinely require tool execution.
"""

from __future__ import annotations

import re

# Action verbs and resource keywords that require tool execution
_ACTION_KEYWORDS = {
    "calendar", "agenda", "schedule", "events", "meeting", "appointments",
    "remind", "reminder", "reminders", "alarm", "routine",
    "inventory", "fridge", "freezer", "pantry", "food", "eat", "snack", "groceries",
    "macro", "macros", "calorie", "calories", "protein", "carbs", "fat", "fiber",
    "water", "bottle", "fluid", "hydration", "drink", "drank",
    "habit", "habits", "streak", "streaks", "pushups", "push-ups", "climb", "climbing", "run", "running",
    "sobriety", "sober", "slip", "relapse", "clean",
    "search", "find", "lookup", "look up", "google", "fetch", "web",
    "memory", "memories", "remember", "dossier", "past chats", "transcripts",
    "file", "files", "workspace", "read", "write", "download",
    "code", "python", "script", "terminal", "shell", "exec", "execute",
    "goal", "goals", "step", "steps",
    "sms", "text", "message", "call", "caller", "phone",
    "who is", "what is", "where is", "when is", "how much", "how many",
}

# Negative / stop / venting command prefixes or patterns
_STOP_PATTERNS = (
    r"\bstop\b", r"\bdon'?t\b", r"\bcancel\b", r"\bnever\s*mind\b",
    r"\bchill\b", r"\bhold\s*on\b", r"\bwait\b", r"\bshut\s*up\b",
    r"\bwhy\s+did\s+you\b", r"\bwhy\s+are\s+you\b", r"\bso\s+dumb\b",
    r"\bits\s+dumb\b", r"\byou'?re\s+dumb\b", r"\bridiculous\b",
    r"\byou\s+suck\b", r"\bthat\s+sucks\b", r"\bthis\s+sucks\b",
    r"\bquit\b",
)

# Pivots that indicate the user stopped one thing but explicitly requested another
_ACTION_PIVOTS = (
    "instead check", "instead look", "instead search", "instead log",
    "instead remind", "instead find", "what is", "how much",
    "can you check", "check my", "log my", "look at my",
)

# Common conversational words and stopwords
_CONVERSATIONAL_WORDS = {
    "wassup", "sup", "yo", "hey", "heyy", "hi", "hello", "howdy",
    "bro", "dude", "man", "mate",
    "gm", "gn", "good", "morning", "afternoon", "evening", "night",
    "lol", "lmao", "haha", "hahaha", "heh", "rofl", "hilarious", "funny",
    "thanks", "thank", "you", "thx", "ty", "appreciate",
    "cool", "nice", "bet", "ok", "okay", "alright", "sweet",
    "sounds", "great", "awesome", "perfect",
    "bye", "cya", "peace", "later", "see", "ya",
    "how", "are", "doing", "is", "it", "going", "was", "that", "this",
    "am", "i", "me", "my", "we", "the", "a", "an", "so", "very", "just",
    "what's", "whats", "up",
}


def should_skip_tools(user_input: str) -> bool:
    """Returns True if the message is purely conversational or a stop/vent command
    that should be answered directly in text with NO tool overhead.
    """
    raw = (user_input or "").strip()
    if not raw:
        return True

    text = raw.lower()
    text = re.sub(r"^[\s\"'`>]+", "", text).strip()

    # 1. Check if user is venting, complaining, or issuing a stop directive
    for pat in _STOP_PATTERNS:
        if re.search(pat, text):
            # If the user explicitly pivots to an action ("stop sending emails, instead check my calendar")
            for pivot in _ACTION_PIVOTS:
                if pivot in text:
                    return False
            # Complaints, rants, and stop directives never need tools
            return True

    # 2. Check if the message contains any explicit action keywords
    words = re.findall(r"\b[\w'-]+\b", text)
    has_action_keyword = False
    for kw in _ACTION_KEYWORDS:
        if " " in kw:
            if kw in text:
                has_action_keyword = True
                break
        else:
            if kw in words:
                has_action_keyword = True
                break

    if has_action_keyword:
        return False

    # 3. Short messages (<= 10 words) consisting primarily of conversational words
    if len(words) <= 10:
        non_conv = [w for w in words if w not in _CONVERSATIONAL_WORDS]
        if len(non_conv) <= 2:
            return True

    # 4. Pure conversational phrases
    if text in {
        "how are you", "how are you doing", "how's it going", "hows it going",
        "who are you", "what can you do", "what's up", "whats up",
        "tell me a joke", "what's the move", "whats the move",
    }:
        return True

    return False
