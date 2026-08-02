from __future__ import annotations

from copy import deepcopy
from typing import Any

from .base import DomainContext


class LocationsDomain:
    name = "locations"

    def get_agent_view(self, state_view: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        locations = state_view.get("world_state", {}).get("map_locations", {})
        user_state = state_view.get("user_state", {})
        user_state["location"] = self._expand(user_state.get("location"), locations)
        for character in state_view.get("characters", {}).values():
            character_state = character.get("state", {})
            character_state["location"] = self._expand(character_state.get("location"), locations)
        return state_view

    def _expand(self, value: Any, locations: Any) -> Any:
        locations = locations if isinstance(locations, dict) else {}
        location_id = value.get("value") if isinstance(value, dict) and "value" in value else value
        if location_id in locations and isinstance(locations[location_id], dict):
            return {"id": location_id, **deepcopy(locations[location_id])}
        return value

    def apply_update(self, target_state: dict[str, Any], update: Any, context: DomainContext) -> dict[str, Any]:
        return {}
