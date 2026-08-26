from __future__ import annotations

import re
from typing import Any

from .character_agent import CharacterAgent


class EnvironmentLayer:
    """Executes director-defined character tasks and gathers feedback."""

    def __init__(self, character_agent: CharacterAgent) -> None:
        self.character_agent = character_agent

    def run(self, characters: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
        feedback_items = []
        turn_dialogue: list[dict[str, Any]] = []
        for group in self.grouped_characters(characters):
            group_entries: list[dict[str, Any]] = []
            for character in group:
                feedback = self.run_character(character, state, turn_dialogue)
                feedback_items.append(feedback)
                group_entries.append(self.dialogue_entry(feedback))
            turn_dialogue.extend(group_entries)
        return self.finalize_feedback(feedback_items)

    def run_character(
        self,
        character: dict[str, Any],
        state: dict[str, Any],
        turn_dialogue: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        character_id = character.get("id")
        if not character_id:
            return {
                "character_id": "",
                "response": "",
                "state_update": {},
                "memory_append": "",
            }
        plan_context = {**character, "turn_dialogue": list(turn_dialogue or [])}
        part = self.character_agent.act(character_id, plan_context, state)
        result = self._dialogue_feedback_from_part(part, character_id)
        character_state = state.get("characters", {}).get(character_id, {}).get("state", {})
        result["name"] = character_state.get("name", character_id)
        result["order"] = character.get("order", 1)
        result["raw_response"] = result.get("response", "")
        result["visible_dialogue"] = self.visible_dialogue(result.get("response", ""))
        result["turn_part"] = part
        return result

    def stream_character(
        self,
        character: dict[str, Any],
        state: dict[str, Any],
        turn_dialogue: list[dict[str, Any]] | None = None,
    ):
        character_id = character.get("id")
        if not character_id:
            return
        plan_context = {**character, "turn_dialogue": list(turn_dialogue or [])}
        yield from self.character_agent.stream_act(character_id, plan_context, state)

    def finalize_feedback(
        self,
        feedback_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        summaries = []
        character_feedback = []
        for feedback in feedback_items:
            if feedback.get("response"):
                summaries.append(f"{feedback['character_id']} responded within the scene.")
            character_feedback.append(
                {
                    key: value
                    for key, value in feedback.items()
                    if key not in {"raw_response", "visible_dialogue", "turn_part"}
                }
            )
        return {
            "character_feedback": character_feedback,
            "env_summary": " ".join(part for part in summaries if part).strip(),
        }

    def ordered_characters(self, characters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        indexed = [(index, character) for index, character in enumerate(characters) if isinstance(character, dict)]
        return [
            character
            for index, character in sorted(
                indexed,
                key=lambda item: (self._safe_order(item[1].get("order"), item[0]), item[0]),
            )
        ]

    def grouped_characters(self, characters: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        groups: list[list[dict[str, Any]]] = []
        current_order: int | None = None
        for index, character in enumerate(self.ordered_characters(characters)):
            order = self._safe_order(character.get("order"), index)
            if current_order != order:
                groups.append([])
                current_order = order
            groups[-1].append(character)
        return groups

    def dialogue_entry(self, feedback: dict[str, Any]) -> dict[str, Any]:
        raw_response = str(feedback.get("raw_response") or feedback.get("response") or "")
        entry = {
            "character_id": str(feedback.get("character_id", "")),
            "raw_response": raw_response,
            "visible_dialogue": str(feedback.get("visible_dialogue") or self.visible_dialogue(raw_response)),
        }
        if feedback.get("audience"):
            entry["audience"] = feedback["audience"]
        return entry

    def _dialogue_feedback_from_part(self, part: dict[str, Any], character_id: str) -> dict[str, Any]:
        character = (part.get("characters", {}) if isinstance(part, dict) else {}).get(character_id, {})
        dialogue = character.get("dialogue", {}) if isinstance(character, dict) else {}
        return {
            "character_id": character_id,
            "name": character.get("name", character_id) if isinstance(character, dict) else character_id,
            "response": dialogue.get("response", "") if isinstance(dialogue, dict) else "",
            "audience": dialogue.get("audience", []) if isinstance(dialogue, dict) else [],
            "raw_text": dialogue.get("raw_text", "") if isinstance(dialogue, dict) else "",
        }

    def visible_dialogue(self, raw_response: str) -> str:
        return re.sub(r"\[[^\]]*\]", "", str(raw_response), flags=re.DOTALL).strip()

    def _safe_order(self, value: Any, fallback_index: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback_index + 1
