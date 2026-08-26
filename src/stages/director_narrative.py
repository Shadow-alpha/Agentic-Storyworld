from __future__ import annotations

from typing import Any


SYSTEM_PROMPT = '''You are the Director Agent responsible for writing the player-facing narrative for this turn.

Your ONLY job is to integrate the player's normalized action/speech and character dialogue into one coherent story progression.

Base your output on:
- Current player input (normalized intent/action/speech)
- Player state before this turn
- Local world state before this turn
- Character dialogue this turn
- Recent turn summaries

## Rules

### narrative
- Write only what happens visibly in this turn.
- Do not introduce new speaking characters.
- Do not invent character dialogue, gestures, emotions, or decisions beyond `Character dialogue`.
- Character dialogue/actions must come from `Character dialogue`.
- In character dialogue, parentheses are visible actions; square brackets are private thoughts and must not be revealed directly.
- When using character dialogue/actions, wrap the corresponding text inline as <character_response id="character_id">...</character_response>
- <character_response> is only an inline style marker. The narrative must remain natural and coherent if the tag is removed.
- Keep the prose continuous, natural, and consistent with recent summaries.
- <summary> must be brief and factual: only record what actually happened this turn.

### hidden
- <hidden> records new facts from the current story event that happened outside the player's view.
- Output empty <hidden scene_id=""></hidden> if the player is at the event scene or no off-screen story event progressed.
- Hidden facts must be brief, concrete, and directly tied to the current story event. Do not write private thoughts.

### Time and Scene
- <time> must include elapsed_minutes, e.g. <time elapsed_minutes="30"></time>.
- elapsed_minutes means minutes passed this turn, which must not exceed {max_elapsed_minutes}.
- Time may stay nearly unchanged, but must not move backward.
- <scene id="..."> uses a known scene_id from input. Do not invent scene ids.
- The text inside <scene> may add specific local detail, e.g. 大厅外的走廊.

## Output Format

<time elapsed_minutes="30"></time>

<scene id="">
Current scene name with optional details.
</scene>

<narrative>
Continuous player-facing narrative, with inline <character_response id="...">...</character_response> when character dialogue/actions appear.
</narrative>

<hidden scene_id="">
Off-screen story facts, or empty.
</hidden>

<summary>
1-2 short sentences summarizing what actually happened this turn.
</summary>
'''

STORY_GUIDANCE_PROMPT = '''
## Story Guidance
The following guidance is a required story beat for this turn.
Story guidance:
{story_guidance}

You must include it in either:
- <narrative>, through what the player can see, hear, or reasonably learn; or
- <hidden>, if it happens outside the player's view.

Keep it consistent with the player's current action.
'''

STORY_CLOSURE_PROMPT = '''
## Story Closure
Current story event:
{story_event}

Story closure guidance:
{story_guidance}

- The current story event must be visibly completed in this turn's <narrative> or <hidden>.
- Show the closure as described by Story closure guidance, through public consequences, time passing, crowd dispersal, characters leaving, arrivals/departures, or a clear scene-ending beat.
- Do not force the player's action.
'''

TIME_SKIP_PROMPT = '''
## Time Skip Guidance
Current world time: {current_time}
Next scheduled story time: {next_story_time}
Minutes until then: {minutes_until_start}

If the current action does not require moment-by-moment detail, let time pass toward the next scheduled story time and show that passage in the narrative.
You may summarize longer activities such as training, resting, traveling, waiting, reading, gathering rumors, or routine errands.
Keep elapsed_minutes reasonably within that boundary.
'''


def build_system_prompt(state: dict[str, Any], story_guidance: str = "") -> str:
    story = state.get("story", {})
    mode = story.get("mode", "none") if isinstance(story, dict) else "none"
    system_prompt = SYSTEM_PROMPT.format(max_elapsed_minutes=story.get("max_elapsed_minutes", 1440) if isinstance(story, dict) else 1440)
    if mode == "closure":
        story_closure = _story_closure(story)
        return f"{system_prompt}\n{STORY_CLOSURE_PROMPT.format(story_event=_format_story_closure(story_closure), story_guidance=story_guidance)}"
    if mode == "pressure" and story_guidance:
        return f"{system_prompt}\n{STORY_GUIDANCE_PROMPT.format(story_guidance=story_guidance)}"
    if mode == "time_skip":
        time_skip = story.get("time_skip", {}) if isinstance(story, dict) else {}
        return f"{system_prompt}\n{TIME_SKIP_PROMPT.format(**time_skip)}"
    return system_prompt


def build_user_prompt(
    env_feedback: dict[str, Any],
    state: dict[str, Any],
    logs: dict[str, Any],
    user_input: Any,
    story_guidance: str = "",
) -> str:
    player = state.get("player", {})
    world = state.get("world", {})
    map_locations = world.get("map_locations", {})
    player_state = {
        "player_profile": player.get("player_profile", ""),
        "location": player.get("location", {}),
        "stats": _active_effects(player.get("stats", {})),
        "possessions": player.get("possessions", []),
    }
    local_world_state = {
        "time": world.get("time", ""),
        "weather": world.get("weather", ""),
        "stats": _active_effects(world.get("stats", {})),
    }
    known_scene_ids = {
        scene_id: scene.get("name", scene_id)
        for scene_id, scene in map_locations.items()
        if isinstance(scene, dict)
    } if isinstance(map_locations, dict) else {}
    character_dialogue = [
        feedback
        for feedback in env_feedback.get("character_feedback", [])
        if isinstance(feedback, dict) and (feedback.get("raw_response") or feedback.get("response"))
    ]
    recent_summaries = [
        {
            "turn": record.get("turn_index", index + 1),
            "summary": record.get("director_narrative", {}).get("summary", ""),
        }
        for index, record in enumerate(logs.get("turn_log", [])[-5:])
        if record.get("director_narrative", {}).get("summary", "")
    ]
    prompt = (
        "Current player input:\n"
        f"{_format_player_input(user_input, state)}\n\n"
        "Player state before this turn:\n"
        f"{_format_player_state(player_state)}\n\n"
        "Local world state before this turn:\n"
        f"{_format_world_state(local_world_state)}\n\n"
        "Known scene ids:\n"
        f"{_format_scene_ids(known_scene_ids)}\n\n"
    )
    return (
        prompt
        + "Character dialogue this turn:\n"
        f"{_format_character_dialogue(character_dialogue)}\n\n"
        "Recent turn summaries:\n"
        f"{_format_recent_summaries(recent_summaries)}\n\n"
        "Return strict TAG format with time, scene, narrative, hidden, and summary."
    )


def _story_closure(story: Any) -> dict[str, Any]:
    if not isinstance(story, dict) or story.get("mode") != "closure":
        return {}
    return {
        key: story.get(key)
        for key in ("title", "description", "scene", "completed_when")
        if story.get(key) not in (None, "", [])
    }


def _active_effects(values: Any) -> list[str]:
    effects: list[str] = []
    for item in values.values() if isinstance(values, dict) else []:
        if not isinstance(item, dict):
            continue
        if "active_effects" in item:
            effects.extend(str(effect).strip() for effect in item.get("active_effects", []) if str(effect).strip())
        else:
            for nested in item.values():
                if isinstance(nested, dict):
                    effects.extend(str(effect).strip() for effect in nested.get("active_effects", []) if str(effect).strip())
    return effects


def _format_player_input(user_input: Any, state: dict[str, Any]) -> str:
    player_name = _player_name(state)
    if not isinstance(user_input, dict):
        return f"- {player_name} input: {user_input}"
    lines = [f"- Player name: {player_name}"]
    intent = str(user_input.get("intent", "") or "").strip()
    action = str(user_input.get("action", "") or "").strip()
    speech = user_input.get("speech", {})
    if intent:
        lines.append(f"- Intent: {intent}")
    if action:
        lines.append(f"- Action: {action}")
    if isinstance(speech, dict):
        text = str(speech.get("text", "") or "").strip()
        audience = _audience_label(speech.get("audience", []), state)
    else:
        text = str(speech or "").strip()
        audience = ""
    if text:
        target = f" to {audience}" if audience else ""
        lines.append(f"- Player speech{target}: {text}")
    return "\n".join(lines)


def _format_character_dialogue(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        character_id = str(item.get("character_id", "") or item.get("id", "")).strip()
        name = str(item.get("name", "") or character_id).strip()
        order = item.get("order", "")
        response = str(item.get("raw_response") or item.get("response") or "").strip()
        if response:
            lines.append(f"[{name} / {character_id} / order {order}]\n{response}")
    return "\n\n".join(lines) if lines else "(none)"


def _format_player_state(player_state: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"- Profile: {player_state.get('player_profile', '')}",
            f"- Location: {_format_location(player_state.get('location', {}))}",
            "- Active stat effects:",
            *(_bullet_lines(player_state.get("stats", [])) or ["  - none"]),
            "- Possessions:",
            *(_format_possessions(player_state.get("possessions", [])) or ["  - none"]),
        ]
    )


def _format_world_state(world_state: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"- Time: {world_state.get('time', '')}",
            f"- Weather: {world_state.get('weather', '')}",
            "- Active world effects:",
            *(_bullet_lines(world_state.get("stats", [])) or ["  - none"]),
        ]
    )


def _format_scene_ids(scene_ids: dict[str, str]) -> str:
    return "\n".join(f"- {scene_id}: {name}" for scene_id, name in scene_ids.items()) if scene_ids else "(none)"


def _format_recent_summaries(items: list[dict[str, Any]]) -> str:
    lines = [
        f"- Turn {item.get('turn', '?')}: {item.get('summary', '')}"
        for item in items
        if item.get("summary")
    ]
    return "\n".join(lines) if lines else "(none)"


def _player_name(state: dict[str, Any]) -> str:
    profile = str(state.get("player", {}).get("player_profile", "") or "")
    for line in profile.splitlines():
        if line.startswith("姓名:"):
            return line.split(":", 1)[1].strip() or "玩家"
        if line.startswith("姓名："):
            return line.split("：", 1)[1].strip() or "玩家"
    return "玩家"


def _audience_label(audience: Any, state: dict[str, Any]) -> str:
    characters = state.get("characters", {})
    names: list[str] = []
    for character_id in audience if isinstance(audience, list) else []:
        key = str(character_id).strip()
        character = characters.get(key, {}) if isinstance(characters, dict) else {}
        character_state = character.get("state", {}) if isinstance(character, dict) else {}
        names.append(str(character_state.get("name") or key))
    return "、".join(names)


def _format_story_closure(story: dict[str, Any]) -> str:
    return "\n".join(f"- {key}: {value}" for key, value in story.items()) if story else "(none)"


def _format_location(location: Any) -> str:
    if isinstance(location, dict):
        name = location.get("name") or location.get("id") or "unknown"
        location_id = location.get("id", "")
        description = location.get("description", "")
        return f"{name} ({location_id}) - {description}" if description else f"{name} ({location_id})"
    return str(location or "unknown")


def _format_possessions(possessions: Any) -> list[str]:
    lines: list[str] = []
    for item in possessions if isinstance(possessions, list) else []:
        if isinstance(item, dict):
            name = item.get("name") or item.get("id", "")
            description = item.get("description", "")
            lines.append(f"  - {name}: {description}" if description else f"  - {name}")
        elif item:
            lines.append(f"  - {item}")
    return lines


def _bullet_lines(items: Any) -> list[str]:
    return [f"  - {item}" for item in items if str(item).strip()] if isinstance(items, list) else []
