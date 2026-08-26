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
            recent_turns = {item.get("turn") for item in parsed["turns"][-self.recent_turn_limit:]}
            character["memory"] = {
                "items": [
                    item
                    for item in parsed["items"]
                    if item.get("tag") in {"core", "event"} or item.get("turn") in recent_turns
                ]
            }
        return state_view

    def apply_update(self, runtime_state: dict[str, Any], turn_record: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        runtime_characters = runtime_state.setdefault("characters", {})
        for character_id, character_record in (turn_record.get("characters") or {}).items():
            cleaned = self._memory_updates(character_record)
            character = runtime_characters.get(character_id, {})
            if not cleaned or not isinstance(character, dict):
                continue
            entry = f"## Turn {context.turn_number}\n\n{cleaned}\n"
            updated = str(character.get("memory", "") or "").rstrip()
            character["memory"] = updated + ("\n\n" if updated else "") + entry
            memory_path = context.runtime_dir / "memory" / f"{character_id}.md"
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            memory_path.write_text(character["memory"], encoding="utf-8")
        return {}

    def _memory_updates(self, character_record: Any) -> str:
        if not isinstance(character_record, dict):
            return ""
        reflection = character_record.get("reflection", {})
        event_memory = character_record.get("event_memory", {})
        parts = [
            self._clean(reflection.get("memory_append", "") if isinstance(reflection, dict) else "", {"turn", "core"}),
            self._clean(event_memory.get("memory_append", "") if isinstance(event_memory, dict) else "", {"event", "core"}),
        ]
        return "\n".join(part for part in parts if part)

    def _clean(self, memory_append: Any, allowed_tags: set[str]) -> str:
        lines: list[str] = []
        for raw_line in str(memory_append or "").splitlines():
            line = raw_line.strip()
            lowered = line.lower()
            if "turn" in allowed_tags and lowered.startswith("[turn]") and line[6:].strip():
                lines.append(f"[turn] {line[6:].strip()}")
            elif "core" in allowed_tags and lowered.startswith("[core]") and line[6:].strip():
                lines.append(f"[core] {line[6:].strip()}")
            elif "event" in allowed_tags and lowered.startswith("[event]") and line[7:].strip():
                lines.append(f"[event] {line[7:].strip()}")
        return "\n".join(lines)

    def _parse(self, memory_text: Any) -> dict[str, list[dict[str, Any]]]:
        parsed: dict[str, list[dict[str, Any]]] = {"core": [], "events": [], "turns": [], "items": []}
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
                item = {"turn": current_turn, "tag": "core", "text": line[6:].strip()}
                parsed["core"].append(item)
                parsed["items"].append(item)
            elif lowered.startswith("[event]") and line[7:].strip():
                item = {"turn": current_turn, "tag": "event", "text": line[7:].strip()}
                parsed["events"].append(item)
                parsed["items"].append(item)
            elif lowered.startswith("[turn]") and line[6:].strip():
                item = {"turn": current_turn, "tag": "turn", "text": line[6:].strip()}
                parsed["turns"].append(item)
                parsed["items"].append(item)
        return parsed

    def _parse_turn_number(self, line: str) -> int | None:
        for token in line.replace("#", " ").split():
            if token.isdigit():
                return int(token)
        return None
