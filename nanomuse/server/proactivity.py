"""Auto (adaptive) proactivity: how often MiniMuse reaches out follows whether you answer.

Every check-in that actually reached the person (a goal pass whose reply was not quiet) is noted.
If the person writes back within a few hours, that check-in was *answered*. The recent answer rate
sets how far the configured interval is stretched or shrunk, and how readily a pass speaks up:

========  ====================================================  =========  =========
answered  meaning                                               interval   speaks
========  ====================================================  =========  =========
60%+      the check-ins are wanted                              x0.5       readily
30-59%    some are                                              x1         normally
10-29%    most are ignored                                      x2         only if needed
under 10% they are being ignored                                x4         only if needed
========  ====================================================  =========  =========

Until a few check-ins have had time to be answered or ignored there is no evidence, and it behaves
like the default level. Reminders the person asked for, and the feed, are not check-ins and never count.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from nanomuse.logger import logger

FILE_NAME = "proactivity_auto.json"
# a check-in is answered by a reply within this long, and ignored once it is older than this
ANSWER_WINDOW_S = 6 * 3600
# the most recent resolved check-ins that count
KEEP = 12
# fewer resolved check-ins than this is no evidence
MIN_EVIDENCE = 4


@dataclass
class Checkin:
    at: float
    answered: bool = False


def factor_for(rate: float) -> float:
    """The interval multiplier for an answer rate between 0 and 1."""
    if rate >= 0.6:
        return 0.5
    if rate >= 0.3:
        return 1.0
    if rate >= 0.1:
        return 2.0
    return 4.0


def level_for(factor: float) -> str:
    """The surfacing level (how readily a pass speaks up) for an interval factor."""
    if factor <= 0.5:
        return "high"
    if factor >= 2.0:
        return "low"
    return "default"


class AutoProactivity:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / FILE_NAME
        self.items: list[Checkin] = []
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8")) if self.path.exists() else []
        except (OSError, ValueError) as exc:
            logger.warning("could not read {}: {}", self.path, exc)
            return
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and isinstance(item.get("at"), int | float):
                    self.items.append(Checkin(float(item["at"]), bool(item.get("answered"))))

    def _save(self) -> None:
        self.items = self.items[-(KEEP * 3) :]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps([{"at": c.at, "answered": c.answered} for c in self.items]), "utf-8"
            )
        except OSError as exc:  # pragma: no cover - a read-only data dir
            logger.warning("could not save {}: {}", self.path, exc)

    def record_sent(self, now: float | None = None) -> None:
        """A check-in reached the person."""
        self.items.append(Checkin(time.time() if now is None else now))
        self._save()

    def record_reply(self, now: float | None = None) -> bool:
        """The person wrote. The newest check-in still inside the window counts as answered."""
        now = time.time() if now is None else now
        for item in reversed(self.items):
            if item.answered:
                continue
            if now - item.at <= ANSWER_WINDOW_S:
                item.answered = True
                self._save()
                return True
            break  # older ones are already past the window
        return False

    def resolved(self, now: float | None = None) -> list[Checkin]:
        """Check-ins whose outcome is known: answered, or old enough to count as ignored."""
        now = time.time() if now is None else now
        done = [c for c in self.items if c.answered or now - c.at > ANSWER_WINDOW_S]
        return done[-KEEP:]

    def stats(self, now: float | None = None) -> dict[str, int]:
        now = time.time() if now is None else now
        done = self.resolved(now)
        return {
            "answered": sum(1 for c in done if c.answered),
            "resolved": len(done),
            # sent recently enough that the person may still answer
            "waiting": sum(1 for c in self.items if not c.answered and now - c.at <= ANSWER_WINDOW_S),
        }

    def factor(self, now: float | None = None) -> float:
        done = self.resolved(now)
        if len(done) < MIN_EVIDENCE:
            return 1.0
        return factor_for(sum(1 for c in done if c.answered) / len(done))
