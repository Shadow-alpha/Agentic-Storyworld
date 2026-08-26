from __future__ import annotations

from typing import Any


SYSTEM_PROMPT = '''You are reflecting as {character_name} (id: {character_id}) after this turn's final narrative.

Your ONLY job is to update your emotion, location, numeric stats/relations, and memory.

## Inputs

You will receive:
- PROFILE
- STATE
- MEMORY
- YOUR RAW RESPONSE
- FINAL NARRATIVE
- KNOWN SCENE IDS

## Rules

- FINAL NARRATIVE is the confirmed outcome.
- Use YOUR RAW RESPONSE only for what you personally said, did, or thought.
- If your response conflicts with FINAL NARRATIVE, follow FINAL NARRATIVE.
- Do not invent off-screen events, hidden motives, or uncertain facts.

### state_update
- <state_update> may include only changed numeric stats/relations. Do not repeat unchanged fields or full state objects. Do not introduce undefined fields.
- Use absolute final values, not relative changes.
- Each state_update line must be: field = absolute_value | short reason. The reason must be based on FINAL NARRATIVE.
- Use relation paths exactly as provided, e.g. relations.player.trust.

### location
- If FINAL NARRATIVE clearly confirms that you moved, output the new known scene id in <location id="...">reason</location>.
- If your location did not clearly change, output empty <location></location>.

### memory_append
- <memory_append> must be first-person confirmed memory. Keep only what may affect future behavior.
- Use [turn] for normal memory. Keep it concise, 1-3 sentences of what you personally experienced this turn.
- Use [core] rarely, only for major relationship shifts, promises, betrayals, or irreversible choices.

## Output Format

<emotion>
Some short emotion labels or phrase, not long explanation.
</emotion>

<location id="...">
brief reason if location changed
</location>

<state_update>
stats.stress = 45 | short reason
relations.player.trust = 20 | short reason
</state_update>

<memory_append>
[turn] ...
[core] ...
</memory_append>
'''


def build_system_prompt(character_id: str, character_context: dict[str, Any]) -> str:
    state_snapshot = character_context.get("state", {})
    return SYSTEM_PROMPT.format(
        character_name=state_snapshot.get("name", character_id),
        character_id=character_id,
    )


def build_user_prompt(
    character_id: str,
    character_context: dict[str, Any],
    reflection_context: dict[str, Any],
) -> str:
    state_snapshot = character_context.get("state", {})
    raw_response = reflection_context.get("raw_response", reflection_context.get("response", ""))
    final_narrative = reflection_context.get(
        "final_narrative",
        reflection_context.get("narrative_result", reflection_context.get("narrative", "")),
    )
    updatable_state = {
        "stats": _without_active_effects(state_snapshot.get("stats", {})),
        "relations": _without_active_effects(state_snapshot.get("relations", {})),
    }
    return (
        "PROFILE:\n"
        f"{character_context.get('profile', '')}\n\n"
        "CURRENT EMOTION:\n"
        f"{state_snapshot.get('emotion') or 'unknown'}\n\n"
        "UPDATABLE STATE:\n"
        f"{_format_updatable_state(updatable_state)}\n\n"
        "MEMORY:\n"
        f"{_format_memory_context(character_context.get('memory'))}\n\n"
        "YOUR RAW RESPONSE:\n"
        f"{_format_value(raw_response)}\n\n"
        "FINAL NARRATIVE:\n"
        f"{_format_value(_narrative_text(final_narrative))}\n\n"
        "KNOWN SCENE IDS:\n"
        f"{_format_scene_ids(reflection_context.get('known_scene_ids', {}))}\n\n"
        "Return strict TAG format with <emotion>, <location>, <state_update>, and <memory_append>."
    )


EVENT_MEMORY_SYSTEM_PROMPT = '''You are {character_name} (id: {character_id}) updating memory after a story event has ended.

Your ONLY job is to append confirmed event memory.

## Rules

- Write in first person as this character.
- Use [event] for the ended story event's confirmed result.
- Use [core] rarely, only if this event permanently changes your belief, loyalty, promise, fear, or relationship.
- Do not invent facts beyond EVENT RESULT.

## Output Format

<memory_append>
[event] ...
[core] ...
</memory_append>
'''


def build_event_memory_system_prompt(character_id: str, character_context: dict[str, Any]) -> str:
    state_snapshot = character_context.get("state", {})
    return EVENT_MEMORY_SYSTEM_PROMPT.format(
        character_name=state_snapshot.get("name", character_id),
        character_id=character_id,
    )


def build_event_memory_user_prompt(
    character_id: str,
    character_context: dict[str, Any],
    event_context: dict[str, Any],
) -> str:
    return (
        "PROFILE:\n"
        f"{character_context.get('profile', '')}\n\n"
        "MEMORY:\n"
        f"{_format_memory_context(character_context.get('memory'))}\n\n"
        "EVENT RESULT:\n"
        f"{_format_event_result(event_context)}\n\n"
        "Return strict TAG format with <memory_append> only."
    )


def _format_memory_context(memory: Any) -> str:
    if isinstance(memory, dict):
        return _format_memory_timeline(memory.get("items", []))
    memory_text = str(memory or "").strip()
    return memory_text[-600:] if memory_text else "(none)"


def _format_memory_timeline(items: Any) -> str:
    lines: list[str] = []
    current_turn: Any = object()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        tag = str(item.get("tag", "turn") or "turn").strip()
        turn = item.get("turn")
        if not text:
            continue
        if turn != current_turn:
            lines.append(f"[Turn {turn}]" if turn else "[Turn ?]")
            current_turn = turn
        lines.append(f"[{tag}] {text}")
    return "\n".join(lines) if lines else "(none)"


def _format_event_result(event_context: Any) -> str:
    if not isinstance(event_context, dict):
        return str(event_context or "")
    lines = [
        f"- story_id: {event_context.get('story_id', '')}",
        f"- title: {event_context.get('title', '')}",
        f"- event_progress: {event_context.get('event_progress', '')}",
    ]
    hidden = str(event_context.get("hidden", "") or "").strip()
    narrative = str(event_context.get("narrative", "") or "").strip()
    lines.append(f"- hidden: {hidden}" if hidden else f"- narrative: {narrative}")
    return "\n".join(line for line in lines if line.strip())


def _format_updatable_state(state: dict[str, Any]) -> str:
    lines: list[str] = []
    for group_name, group in state.items():
        if not isinstance(group, dict) or not group:
            continue
        lines.append(f"{group_name}:")
        for key, item in group.items():
            _append_state_item(lines, str(key), item)
    return "\n".join(lines) if lines else "(none)"


def _append_state_item(lines: list[str], key: str, item: Any, indent: str = "- ") -> None:
    if not isinstance(item, dict):
        lines.append(f"{indent}{key}: {item}")
        return
    if "value" in item:
        lines.append(f"{indent}{key}: value={item.get('value', '')}")
        if item.get("description"):
            lines.append(f"  description: {item['description']}")
        if item.get("update_guidance"):
            lines.append(f"  update_guidance: {item['update_guidance']}")
        return
    lines.append(f"{indent}{key}:")
    for child_key, child_value in item.items():
        _append_state_item(lines, str(child_key), child_value, "  - ")


def _format_scene_ids(scene_ids: Any) -> str:
    if not isinstance(scene_ids, dict) or not scene_ids:
        return "(none)"
    return "\n".join(f"- {scene_id}: {name}" for scene_id, name in scene_ids.items())


def _without_active_effects(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_active_effects(item) for key, item in value.items() if key != "active_effects"}
    if isinstance(value, list):
        return [_without_active_effects(item) for item in value]
    return value


def _narrative_text(final_narrative: Any) -> str:
    if isinstance(final_narrative, dict):
        narrative = final_narrative.get("narrative", final_narrative)
        if isinstance(narrative, dict):
            return str(narrative.get("visible", "") or "")
        return str(narrative or "")
    return str(final_narrative or "")


def _format_value(value: Any) -> str:
    return str(value or "")
