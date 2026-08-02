from __future__ import annotations

from typing import Any

from .base import DomainContext


class MemoryDomain:
    name = "memory"

    def __init__(self, recent_turn_limit: int = 5) -> None:
        self.recent_turn_limit = recent_turn_limit

    def get_agent_view(self, state_view: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        for character in state_view.get("characters", {}).values():
            parsed = self._parse(character.get("memory", ""))
            character["memory"] = {"core": parsed["core"], "turns": parsed["turns"][-self.recent_turn_limit:]}
        return state_view

    def apply_update(self, target_state: dict[str, Any], update: Any, context: DomainContext) -> dict[str, Any]:
        return {}

    def _parse(self, memory_text: Any) -> dict[str, list[dict[str, Any]]]:
        parsed: dict[str, list[dict[str, Any]]] = {"core": [], "turns": []}
        current_turn: int | None = None
        for raw_line in str(memory_text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.lower().startswith("## turn"):
                current_turn = self._parse_turn_number(line)
                continue
            lowered = line.lower()
            if lowered.startswith("[core]") and line[6:].strip():
                parsed["core"].append({"turn": current_turn, "text": line[6:].strip()})
            elif lowered.startswith("[turn]") and line[6:].strip():
                parsed["turns"].append({"turn": current_turn, "text": line[6:].strip()})
        return parsed

    def _parse_turn_number(self, line: str) -> int | None:
        for token in line.replace("#", " ").split():
            if token.isdigit():
                return int(token)
        return None
