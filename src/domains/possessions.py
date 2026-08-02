from __future__ import annotations

from copy import deepcopy
from typing import Any

from .base import DomainContext


class PossessionsDomain:
    name = "possessions"
    update_tag = "item_update"
    update_key = "possessions"

    def get_agent_view(self, state_view: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        user_state = state_view.get("user_state", {})
        user_state["possessions"] = self._expand(user_state.get("possessions", []), context)
        for character in state_view.get("characters", {}).values():
            character_state = character.get("state", {})
            character_state["possessions"] = self._expand(character_state.get("possessions", []), context)
        return state_view

    def _expand(self, value: Any, context: DomainContext) -> list[dict[str, Any]]:
        items = context.config.get("items", {})
        if not isinstance(value, list) or not isinstance(items, dict):
            return []
        expanded = []
        for item in value:
            item_id = str(item.get("id") if isinstance(item, dict) else item).strip()
            if not item_id:
                continue
            item_config = items.get(item_id, {})
            expanded.append({"id": item_id, **deepcopy(item_config)} if isinstance(item_config, dict) else {"id": item_id})
        return expanded

    def apply_update(self, target_state: dict[str, Any], update: Any, context: DomainContext) -> dict[str, Any]:
        # Possession mutation protocol is intentionally not enabled yet.
        return {}
