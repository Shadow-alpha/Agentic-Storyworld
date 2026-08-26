from __future__ import annotations

from typing import Any

from .base import DomainContext


class CharactersDomain:
    name = "characters"

    def get_agent_view(self, state_view: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        return state_view

    def apply_update(self, runtime_state: dict[str, Any], turn_record: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        changes: dict[str, Any] = {"characters": {}}
        runtime_characters = runtime_state.setdefault("characters", {})
        for character_id, character_record in (turn_record.get("characters") or {}).items():
            reflection = character_record.get("reflection", {}) if isinstance(character_record, dict) else {}
            emotion = str(reflection.get("emotion") or "").strip() if isinstance(reflection, dict) else ""
            character = runtime_characters.get(character_id, {})
            state = character.get("state", {}) if isinstance(character, dict) else {}
            if emotion and state.get("emotion") != emotion:
                character_changes: dict[str, Any] = {}
                context.record_change(character_changes, ("emotion",), state.get("emotion"), emotion)
                state["emotion"] = emotion
                changes["characters"][character_id] = character_changes
        return changes if changes["characters"] else {}
