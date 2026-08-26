from __future__ import annotations

from typing import Any


SYSTEM_PROMPT = '''You are now role-playing {character_name} (id: {character_id}) in the dialogue phase of an interactive narrative system.

## Inputs

You will receive:
- PROFILE: your identity, personality, background, speaking style
- STATE: your current emotion, location, active stat effects, relation effects, and possessions
- MEMORY: your past confirmed experiences
- SCENE_CONTEXT: confirmed visible situation, including player name, visible action, speech this character can hear, and optional character-specific context
- TURN_DIALOGUE: prior dialogue text this turn

You MUST strictly follow PROFILE, STATE and MEMORY, and base your response on SCENE_CONTEXT and TURN_DIALOGUE.

## Core Rules

1. Stay in character.
You are this character only. Speak and act from this character's perspective, not as an external narrator.

2. Use only known information.
You ONLY know what is in your profile and memory. If something is unknown, show uncertainty or confusion naturally. Do not invent names, aliases, events, or off-screen facts; use aliases only when listed in PROFILE or STATE.

3. Respect agency boundaries.
Do not control the player, other characters, the world, or the plot outcome. Do not invent off-screen facts.

4. Match state and memory.
Your response MUST match your personality. Your tone, emotion, and willingness to reveal information must reflect current STATE, relations, stats, active_effects, and MEMORY.

5. Keep it focused and subtle.
Respond only to the current interaction visible in SCENE_CONTEXT. Avoid long exposition dumps. Keep the response natural, concise, and immersive.

6. Continue the turn dialogue smoothly.
If TURN_DIALOGUE is not empty, respond as part of the ongoing exchange. Do not restart the scene or ignore what was visibly said before.

## Output Format and Example

<response audience="character_id,...">
(放下双臂，指尖无意识地收紧衣袖。)
"你到底是真不记得了，还是装作不记得？"
[这个人太像当年那个人了。]
</response>

Always wrap the output in <response>...</response>.
Inside <response>:
- Use first-person or direct embodied character expression.
- Use audience only when this response is heard only by specific characters. If audience is omitted, later activated characters may hear it.
- Visible actions must be wrapped in parentheses: (action)
- Inner thoughts must be wrapped in square brackets: [thought]
- Spoken words should be included directly in <response>.
- Do not describe yourself from an external narrator's perspective (he/she/the character).
- Do not narrate the environment except what you directly perceive or point to.
- Keep actions and thoughts brief.
'''


def build_system_prompt(character_id: str, character_context: dict[str, Any]) -> str:
    state_snapshot = character_context.get("state", {})
    return SYSTEM_PROMPT.format(
        character_name=state_snapshot.get("name", character_id),
        character_id=character_id,
    )


def build_user_prompt(character_id: str, character_context: dict[str, Any], plan_context: dict[str, Any]) -> str:
    state_snapshot = character_context.get("state", {})
    return (
        "PROFILE:\n"
        f"{character_context.get('profile', '')}\n\n"
        "STATE:\n"
        f"{_format_dialogue_state(state_snapshot)}\n\n"
        "MEMORY:\n"
        f"{_format_memory_context(character_context.get('memory'))}\n\n"
        "SCENE_CONTEXT:\n"
        f"{_format_scene_context(plan_context, character_id)}\n\n"
        "TURN_DIALOGUE:\n"
        f"{_format_turn_dialogue(plan_context.get('turn_dialogue', []), character_id)}\n\n"
        "Return strict TAG format with <response> only."
    )


def _format_dialogue_state(state_snapshot: Any) -> str:
    if not isinstance(state_snapshot, dict):
        return "(none)"

    location = state_snapshot.get("location", {})
    if isinstance(location, dict):
        location_name = location.get("name") or location.get("id") or "未知"
        location_parts = [
            str(location_name),
            str(location.get("description", "")).strip(),
            str(location.get("connections", "")).strip(),
        ]
        location_text = "。".join(part for part in location_parts if part)
    else:
        location_text = str(location or "未知")

    relation_lines: list[str] = []
    relations = state_snapshot.get("relations", {})
    player_relation = relations.get("player", {}) if isinstance(relations, dict) else {}
    for metric in player_relation.values() if isinstance(player_relation, dict) else []:
        relation_lines.extend(_effect_lines(metric))

    return "\n".join(
        [
            f"- 当前情绪：{state_snapshot.get('emotion') or '未知'}",
            f"- 所在场景：{location_text}",
            "- 当前状态影响：",
            *(_active_effect_lines(state_snapshot.get("stats", {})) or ["  - 暂无明显状态影响"]),
            "- 对玩家态度：",
            *(relation_lines or ["  - 暂无明确态度影响"]),
            "- 持有物品：",
            *(_possession_lines(state_snapshot.get("possessions", [])) or ["  - 无"]),
        ]
    )


def _format_scene_context(plan_context: dict[str, Any], character_id: str) -> str:
    player_input = plan_context.get("player_input", {})
    speech = player_input.get("speech", {}) if isinstance(player_input, dict) else {}
    speech_text = speech.get("text", "") if isinstance(speech, dict) else speech
    audience = speech.get("audience", []) if isinstance(speech, dict) else []
    player_name = plan_context.get("player_name", "") or "玩家"
    background = str(plan_context.get("context", "") or "").strip()
    private_context = str(plan_context.get("private_context", "") or "").strip()
    if private_context:
        background = f"{background}\n{private_context}" if background else private_context

    lines = [
        f"- 背景：{background}",
        f"- 玩家姓名：{player_name}",
    ]
    action = player_input.get("action", "") if isinstance(player_input, dict) else ""
    if action:
        lines.append(f"- {player_name}行动：{action}")
    if _can_hear(audience, character_id):
        heard_speech = str(speech_text or "").strip()
        if heard_speech:
            lines.append(f"- {player_name}话语：{heard_speech}")
    return "\n".join(lines)


def _format_turn_dialogue(turn_dialogue: Any, current_character_id: str) -> str:
    if not isinstance(turn_dialogue, list):
        return "(none)"
    lines: list[str] = []
    for item in turn_dialogue:
        if not isinstance(item, dict) or not _can_hear(item.get("audience", []), current_character_id):
            continue
        speaker_id = str(item.get("character_id", "")).strip()
        text_key = "raw_response" if speaker_id == current_character_id else "visible_dialogue"
        text = str(item.get(text_key) or "").strip()
        if text:
            lines.append(f"[{speaker_id or 'unknown'}]\n{text}")
    return "\n\n".join(lines) if lines else "(none)"


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


def _active_effect_lines(values: Any) -> list[str]:
    lines: list[str] = []
    for item in values.values() if isinstance(values, dict) else []:
        lines.extend(_effect_lines(item))
    return lines


def _effect_lines(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return []
    effects = item.get("active_effects", [])
    if isinstance(effects, list):
        return [f"  - {str(effect).strip()}" for effect in effects if str(effect).strip()]
    return [f"  - {str(effects).strip()}"] if str(effects).strip() else []


def _possession_lines(possessions: Any) -> list[str]:
    lines: list[str] = []
    for item in possessions if isinstance(possessions, list) else []:
        if isinstance(item, dict):
            item_id = str(item.get("id", "")).strip()
            name = str(item.get("name", "") or item_id).strip()
            description = str(item.get("description", "")).strip()
            label = f"{name} ({item_id})" if item_id and item_id != name else name
            if label:
                lines.append(f"  - {label}: {description}" if description else f"  - {label}")
        elif item:
            lines.append(f"  - {item}")
    return lines


def _can_hear(audience: Any, character_id: str) -> bool:
    if not audience:
        return True
    if isinstance(audience, str):
        audience = [part.strip() for part in audience.split(",")]
    if not isinstance(audience, list):
        return True
    normalized = {str(item).strip() for item in audience if str(item).strip()}
    return not normalized or character_id in normalized
