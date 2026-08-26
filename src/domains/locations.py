from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .base import DomainContext


def advance_time(current_time: Any, elapsed_minutes: Any) -> str:
    match = re.match(r"^\s*Day\s*(\d+)\s+(\d{1,2}):(\d{1,2})\s*$", str(current_time or ""))
    if not match:
        return str(current_time or "").strip()
    day, hour, minute = (int(part) for part in match.groups())
    absolute_minutes = (day - 1) * 1440 + hour * 60 + minute + max(0, safe_int(elapsed_minutes))
    day = absolute_minutes // 1440 + 1
    minute_of_day = absolute_minutes % 1440
    return f"Day{day} {minute_of_day // 60:02d}:{minute_of_day % 60:02d}"


def safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


class LocationsDomain:
    name = "locations"

    def get_agent_view(self, state_view: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        locations = state_view.get("world", {}).get("map_locations", {})
        player = state_view.get("player", {})
        player["location"] = self._expand(player.get("location"), locations)
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

    def apply_update(self, runtime_state: dict[str, Any], turn_record: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        changes: dict[str, Any] = {"world": {}, "player": {}, "characters": {}}
        narrative = turn_record.get("director_narrative", {})
        if isinstance(narrative, dict):
            time_value = narrative.get("time", {})
            world = runtime_state.setdefault("world", {})
            old_time = world.get("time")
            elapsed_minutes = self._elapsed_minutes(time_value)
            world["total_turns"] = safe_int(world.get("total_turns")) + 1
            world["total_minutes"] = safe_int(world.get("total_minutes")) + elapsed_minutes
            time_text = advance_time(old_time, elapsed_minutes)
            if time_text:
                context.record_change(changes["world"], ("time",), old_time, time_text)
                world["time"] = time_text

            scene = narrative.get("scene", {})
            scene_id = scene.get("id", "") if isinstance(scene, dict) else ""
            if self._known_scene(scene_id, context):
                player = runtime_state.setdefault("player", {})
                context.record_change(changes["player"], ("location",), player.get("location"), scene_id)
                player["location"] = scene_id

        runtime_characters = runtime_state.setdefault("characters", {})
        for character_id, character_record in (turn_record.get("characters") or {}).items():
            reflection = character_record.get("reflection", {}) if isinstance(character_record, dict) else {}
            location = reflection.get("location", {}) if isinstance(reflection, dict) else {}
            location_id = self._location_id(location)
            if not self._known_scene(location_id, context):
                continue
            character = runtime_characters.get(character_id, {})
            character_state = character.get("state", {}) if isinstance(character, dict) else {}
            old_location = character_state.get("location")
            character_state["location"] = location_id
            character_changes: dict[str, Any] = {}
            context.record_change(character_changes, ("location",), old_location, location_id, self._reason(location))
            if character_changes:
                changes["characters"].setdefault(character_id, {}).update(character_changes)
        return {key: value for key, value in changes.items() if value}

    def _known_scene(self, scene_id: Any, context: DomainContext) -> bool:
        locations = context.static_state.get("world", {}).get("map_locations", {})
        return bool(scene_id) and isinstance(locations, dict) and scene_id in locations

    def _location_id(self, value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("value") or value.get("id") or "").strip()
        return str(value or "").strip()

    def _reason(self, value: Any) -> str:
        return str(value.get("reason", "") or "").strip() if isinstance(value, dict) else ""

    def _elapsed_minutes(self, value: Any) -> int:
        if not isinstance(value, dict):
            return 0
        return max(0, safe_int(value.get("elapsed_minutes")))
