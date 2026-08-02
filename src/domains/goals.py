from __future__ import annotations

from copy import deepcopy
from typing import Any

from .base import DomainContext


class GoalsDomain:
    name = "goals"
    CHECKPOINT_STATUSES = ("unstarted", "available", "in_progress", "completed")

    def get_agent_view(self, state_view: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        state_view["goals"] = self._agent_goals(state_view.get("goals", {}), context)
        return state_view

    def _agent_goals(self, runtime_goals: Any, context: DomainContext) -> dict[str, Any]:
        definitions = self._definitions(context)
        active_goals = runtime_goals.get("active_goals", {}) if isinstance(runtime_goals, dict) else {}
        if not isinstance(active_goals, dict):
            return {}
        items: dict[str, Any] = {}
        for goal_id, runtime_checkpoints in active_goals.items():
            source_goal = definitions.get(goal_id)
            if not isinstance(source_goal, dict):
                continue
            item = {
                "title": source_goal.get("title", ""),
                "description": source_goal.get("description", ""),
            }
            if source_goal.get("type"):
                item["type"] = source_goal.get("type")
            checkpoints = deepcopy(source_goal.get("checkpoints", []))
            item["checkpoints"] = self._merge_checkpoint_state(checkpoints, runtime_checkpoints)
            items[goal_id] = item
        return {"active_goals": items}

    def get_ui_view(self, runtime_goals: Any, context: DomainContext) -> dict[str, Any]:
        base_goals = context.static_state.get("goals", {})
        runtime_goals = runtime_goals if isinstance(runtime_goals, dict) else {}
        return {
            "definitions": deepcopy(base_goals.get("goals", {})) if isinstance(base_goals, dict) else {},
            "endings": deepcopy(base_goals.get("endings", {})) if isinstance(base_goals, dict) else {},
            "active_goals": deepcopy(runtime_goals.get("active_goals", {})) if isinstance(runtime_goals.get("active_goals"), dict) else {},
            "available_goals": deepcopy(runtime_goals.get("available_goals", {})) if isinstance(runtime_goals.get("available_goals"), dict) else {},
            "completed_goals": list(runtime_goals.get("completed_goals", [])) if isinstance(runtime_goals.get("completed_goals"), list) else [],
            "ending_state": deepcopy(runtime_goals.get("ending_state", {})) if isinstance(runtime_goals.get("ending_state"), dict) else {},
        }

    def apply_update(self, target_state: dict[str, Any], update: Any, context: DomainContext) -> dict[str, Any]:
        return {}

    def _definitions(self, context: DomainContext) -> dict[str, Any]:
        goals = context.static_state.get("goals", {})
        definitions = goals.get("goals", {}) if isinstance(goals, dict) else {}
        return definitions if isinstance(definitions, dict) else {}

    def _merge_checkpoint_state(
        self,
        checkpoints: Any,
        runtime_checkpoints: Any,
    ) -> list[dict[str, Any]]:
        if not isinstance(checkpoints, list):
            return []
        merged = []
        for checkpoint in checkpoints:
            if not isinstance(checkpoint, dict):
                continue
            item = deepcopy(checkpoint)
            state = runtime_checkpoints.get(item.get("id"), {}) if isinstance(runtime_checkpoints, dict) else {}
            if isinstance(state, dict):
                item.update(deepcopy(state))
            item["status"] = self._normalize_status(item.get("status", "unstarted"))
            item.setdefault("progress_note", "")
            merged.append(item)
        return merged

    def _normalize_status(self, status: Any) -> str:
        status = str(status or "unstarted").strip().lower()
        return status if status in self.CHECKPOINT_STATUSES else "in_progress"
