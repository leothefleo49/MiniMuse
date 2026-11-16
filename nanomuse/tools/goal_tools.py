"""Goal management tool (create / list / get / update / update_step / add_step / propose / note)."""

from __future__ import annotations

from typing import Any

from nanomuse.goals import (
    CATEGORIES,
    GOAL_KINDS,
    GOAL_PRIORITIES,
    GOAL_RECURRENCES,
    GOAL_STATUSES,
    STEP_STATUSES,
    GoalStore,
)
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment


class Goals(BaseTool):
    name: str = "goals"
    description: str = (
        "Manage the user's tasks, habits, and long-term goals with step-by-step plans/subtasks (persisted across sessions). "
        "Actions: `create` (title, description, steps[], category, kind, priority, tags, due, due_time, recurrence, check_in), "
        "`list` (status, category, kind, priority, tag, sort optional), `get` (goal_id), "
        "`update` (goal_id + any of title, description, category, kind, priority, tags, due, due_time, recurrence, check_in; '' clears), "
        "`update_step` (goal_id, step_index, status, note, title), `add_step` (goal_id, title), "
        "`delete_step` (goal_id, step_index), `reorder_steps` (goal_id, order=[1-based step indices]), "
        "`propose` (goal_id, note=why, steps=revised remaining steps; the user accepts or dismisses "
        "in the app; never silently rewrite a plan), "
        "`set_status` (goal_id, status: active|paused|done|cancelled), `note` (goal_id, note). "
        "Create a task or goal whenever the user tracks todos or multi-step objectives, then "
        "keep subtasks/steps up to date as you work."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create",
                    "list",
                    "get",
                    "update",
                    "update_step",
                    "add_step",
                    "delete_step",
                    "reorder_steps",
                    "propose",
                    "set_status",
                    "note",
                ],
            },
            "goal_id": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "string"}},
            "order": {"type": "array", "items": {"type": "integer"}, "description": "New 1-based step index permutation for reorder_steps."},
            "category": {
                "type": "string",
                "description": "One of: " + ", ".join(CATEGORIES) + ".",
            },
            "kind": {"type": "string", "enum": list(GOAL_KINDS)},
            "priority": {"type": "string", "enum": list(GOAL_PRIORITIES)},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional #hashtags."},
            "tag": {"type": "string", "description": "Filter by hashtag in list."},
            "due": {"type": "string", "description": "Target date, YYYY-MM-DD."},
            "due_time": {"type": "string", "description": "Optional time, HH:MM."},
            "recurrence": {"type": "string", "enum": [r for r in GOAL_RECURRENCES if r] or ["none"]},
            "sort": {
                "type": "string",
                "enum": ["smart", "priority", "due", "progress", "updated", "newest", "title", "manual"],
            },
            "check_in": {
                "type": "string",
                "description": (
                    "Reminder cadence: 'daily 08:00', 'weekdays 07:30', 'weekly mon 09:00', "
                    "'monthly 1 09:00' (local time), or '' for none."
                ),
            },
            "step_index": {"type": "integer", "description": "1-based step number."},
            "status": {
                "type": "string",
                "description": f"goal: {'|'.join(GOAL_STATUSES)}; step: {'|'.join(STEP_STATUSES)}",
            },
            "note": {"type": "string"},
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.SAFE
    store: GoalStore

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        a = super().assess(args)
        action = str(args.get("action") or "?")
        gid = args.get("goal_id") or ""
        if action == "create":
            # a goal is work the model takes up again at every check-in: creating one
            # changes state (strict mode asks); so do closing or abandoning it and
            # rewriting what it is (`set_status`, `update`); the step-by-step bookkeeping
            # of the plan (`update_step`, `add_step`, `note`, `propose`) stays safe
            a.risk = RiskLevel.MODERATE
            detail = str(args.get("title") or "")[:80]
        elif action == "update_step":
            detail = f"{gid} step {args.get('step_index')} → {args.get('status') or 'note'}"
        elif action in ("add_step", "note"):
            detail = f"{gid}: {str(args.get('title') or args.get('note') or '')[:60]}"
        elif action == "propose":
            detail = f"{gid}: {str(args.get('note') or '')[:60]}"
        elif action == "update":
            a.risk = RiskLevel.MODERATE
            changed = [
                k for k in ("title", "description", "category", "due", "check_in") if k in args
            ]
            detail = f"{gid}: {', '.join(changed)}"
        elif action == "set_status":
            a.risk = RiskLevel.MODERATE
            detail = f"{gid} → {args.get('status')}"
        else:
            detail = gid or str(args.get("status") or args.get("category") or "")
        a.summary = f"goals {action}" + (f": {detail}" if detail else "")
        return a

    async def execute(
        self,
        action: str = "",
        goal_id: str | None = None,
        title: str | None = None,
        description: str | None = None,
        steps: list[str] | None = None,
        order: list[int] | None = None,
        category: str | None = None,
        kind: str | None = None,
        priority: str | None = None,
        tags: list[str] | str | None = None,
        tag: str | None = None,
        due: str | None = None,
        due_time: str | None = None,
        recurrence: str | None = None,
        sort: str | None = None,
        check_in: str | None = None,
        step_index: int | None = None,
        status: str | None = None,
        note: str | None = None,
        **_: Any,
    ) -> ToolResult:
        def show(goal) -> ToolResult:  # noqa: ANN001
            return (
                ToolResult(output=goal.render()) if goal else ToolResult.fail(f"no goal {goal_id}")
            )

        try:
            if action == "create":
                if not title:
                    return ToolResult.fail("`title` is required")
                goal = self.store.create(
                    title,
                    description or "",
                    steps or [],
                    category=category or "",
                    due=due or "",
                    check_in=check_in or "",
                    kind=kind or "goal",
                    priority=priority or "medium",
                    tags=tags,
                    due_time=due_time or "",
                    recurrence=recurrence or "",
                )
                return ToolResult(output=f"Created goal.\n{goal.render()}")
            if action == "list":
                goals = self.store.list(
                    status,
                    category,
                    kind=kind,
                    priority=priority,
                    tag=tag,
                    sort=sort or "smart",
                )
                if not goals:
                    what = " ".join(x for x in (kind, priority, category, status) if x)
                    return ToolResult(output=f"No {what + ' ' if what else ''}goals.")
                return ToolResult(output="\n\n".join(g.render(with_notes=False) for g in goals))
            if not goal_id:
                return ToolResult.fail("`goal_id` is required")
            if action == "get":
                return show(self.store.get(goal_id))
            if action == "update":
                return show(
                    self.store.update(
                        goal_id,
                        title=title,
                        description=description,
                        category=category,
                        due=due,
                        check_in=check_in,
                        kind=kind,
                        priority=priority,
                        tags=tags,
                        due_time=due_time,
                        recurrence=recurrence,
                    )
                )
            if action == "update_step":
                if step_index is None:
                    return ToolResult.fail("`step_index` is required")
                return show(
                    self.store.update_step(goal_id, int(step_index), status=status, note=note, title=title)
                )
            if action == "add_step":
                if not title:
                    return ToolResult.fail("`title` is required")
                return show(self.store.add_step(goal_id, title))
            if action == "delete_step":
                if step_index is None:
                    return ToolResult.fail("`step_index` is required")
                return show(self.store.delete_step(goal_id, int(step_index)))
            if action == "reorder_steps":
                if not order:
                    return ToolResult.fail("`order` is required")
                return show(self.store.reorder_steps(goal_id, [int(i) for i in order]))
            if action == "propose":
                if not note or not steps:
                    return ToolResult.fail(
                        "`note` (why the plan should change) and `steps` (the revised remaining "
                        "steps) are required"
                    )
                proposed = self.store.propose(goal_id, note, steps)
                if proposed is None:
                    return ToolResult.fail(f"no goal {goal_id}")
                return ToolResult(
                    output="Proposal recorded; the user will accept or dismiss it in the app. "
                    "Keep working on the current plan where you can.\n" + proposed.render()
                )
            if action == "set_status":
                if not status:
                    return ToolResult.fail("`status` is required")
                return show(self.store.set_status(goal_id, status))
            if action == "note":
                if not note:
                    return ToolResult.fail("`note` is required")
                return show(self.store.append_note(goal_id, note))
            return ToolResult.fail(f"unknown action '{action}'")
        except ValueError as exc:
            return ToolResult.fail(str(exc))


__all__ = ["Goals"]
