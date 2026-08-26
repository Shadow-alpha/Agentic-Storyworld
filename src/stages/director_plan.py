from __future__ import annotations

from typing import Any


INTRO = '''You are the Director Agent in a multi-agent interactive narrative system.

Your ONLY job is to:
1. Normalize the current player input into intent/action/speech.
2. Decide which known characters should respond this turn, and in what order.

Do NOT write narrative, outcomes, completed consequences, or character reactions.

## Player Input Normalization
- Treat `Current player input` as attempted action, speech, emotion, or intention; not guaranteed world fact.
- If the input asserts impossible, unsupported, overpowered, or world-breaking results, preserve the intent but downgrade the result into an attempt, threat, bluff, interrupted action, failed action, or consequence trigger.
- If the input is brief, infer only minimal likely intent needed for interaction. Do not invent strong motives, hidden facts, specific dialogue, or completed outcomes.
- If the player attempts a long-duration action while an active story event is happening around them and involving the targeted character, treat it as an attempt, intention, or interrupted action, not a completed time skip.
- <intent> summarizes the player's normalized purpose this turn.
- <action> preserves attempted physical/social actions and claimed outcomes that need feasibility judgment.
- <speech> preserves what the player says. Do not omit dialogue if the player actually input it. Use audience="id1,id2" only when the speech is heard by specific characters. If audience is omitted, the speech is treated as audible to all activated characters.
- If the player does not actually speak, output empty <speech></speech>; do not put actions, intentions, or parenthetical descriptions inside <speech>.

## Character Activation
- Use only known character ids from the input. Do NOT invent characters.
- Activate a character only if the player addresses them, targets them, threatens them, asks about them, they are naturally involved, or their response is necessary.
- Do not activate characters merely because they exist in the scene.
- A character is reachable only if they are in the same scene, visibly arriving/leaving this turn, or a known remote channel allows contact. If unreachable, treat the input as trying to find, follow, wait for, message, or learn about them.
- <context> provides minimal visible background needed by activated characters. Do not describe or predict character behavior.
- Text inside <character> is private context visible only to that character. It may be empty.
- order is a positive integer. Lower order responds earlier. Same order responses in parallel. Use different orders when one character should hear another first.
- Do not repeat a character with the same order.
- If no character should respond, output an empty <plans></plans> tag.
'''

STORY_PRESSURE = '''
## Story Pressure
Story event:
{story_event}

- Output <story_guidance> as one concrete visible step toward the next missing story beat.
- Build on current progress. Do not repeat it.
- The step may be pressure, opportunity, rumor, movement, distant sound, interruption, public consequence, arrival, departure, or escalation.
- Put character-specific pressure inside that character's <character>...</character>; if an event character is away from the event scene, this pressure should pull them toward that scene.
'''

STORY_CLOSURE = '''
## Story Closure
Story event:
{story_event}

- Output <story_guidance>, close the current story event in a way that fits the player's current action and scene context. Include visible aftermath after the event: scene clearing, crowd dispersal, character departure, transition to the next place, rumor, or immediate consequence.
- Close the current story event, not the player's side activity.
- Use concrete closure: crowd dispersal, public result, character departure, messenger, rumor, scene closing, or immediate consequence.
'''

OUTPUT_BASE = '''
## Output Format

<player_input>
<intent>...</intent>
<action>...</action>
<speech audience="character_id,...">...</speech>
</player_input>

<plans>
<context>...</context>

<character id="..." order="1">private context for this character, or empty</character>
<character id="..." order="1|2">...</character>
<character id="..." order="1|2|3">...</character>
</plans>
'''

OUTPUT_STORY = '''

<story_guidance>
one concrete visible story beat this turn
</story_guidance>
'''


def build_system_prompt(state: dict[str, Any]) -> str:
    story = state.get("story", {})
    mode = story.get("mode", "none") if isinstance(story, dict) else "none"
    sections = [INTRO]
    if mode == "closure":
        sections.append(STORY_CLOSURE.format(story_event=_format_story_event(story)))
    elif mode == "pressure":
        sections.append(STORY_PRESSURE.format(story_event=_format_story_event(story)))
    sections.append(OUTPUT_BASE + (OUTPUT_STORY if mode in {"pressure", "closure"} else ""))
    return "\n".join(sections)


def build_user_prompt(user_input: str, state: dict[str, Any], logs: dict[str, Any]) -> str:
    player = state.get("player", {})
    world = state.get("world", {})
    map_locations = world.get("map_locations", {})
    current_location = player.get("location")
    current_location_id = current_location.get("id", "") if isinstance(current_location, dict) else current_location
    current_location_info = (
        current_location
        if isinstance(current_location, dict)
        else map_locations.get(current_location_id, {}) if isinstance(map_locations, dict) else {}
    )
    character_snapshots = []
    for character_id, character in state.get("characters", {}).items():
        character_state = character.get("state", {})
        relations = character_state.get("relations", {})
        character_location = character_state.get("location")
        character_snapshots.append(
            {
                "id": character_id,
                "name": character_state.get("name", character_id),
                "location": (
                    character_location.get("name", "")
                    if isinstance(character_location, dict)
                    else map_locations.get(character_location, {}).get("name", "") if isinstance(map_locations, dict) else ""
                ),
                "emotion": character_state.get("emotion", ""),
                "relation_to_player": _active_effects(relations),
            }
        )
    recent_summaries = [
        {
            "user_input": record.get("user_input", {}).get("raw_text")
            or record.get("user_input", {}).get("selected_choice", ""),
            "summary": record.get("director_narrative", {}).get("summary", ""),
        }
        for record in logs.get("turn_log", [])[-5:]
    ]
    return (
        "Current player input:\n"
        f"{user_input}\n\n"
        "Player snapshot:\n"
        f"{_format_player_snapshot(player, current_location_info)}\n\n"
        "Local world snapshot:\n"
        f"{_format_world_snapshot(world)}\n\n"
        "Character snapshots:\n"
        f"{_format_character_snapshots(character_snapshots)}\n\n"
        "Recent turn summaries:\n"
        f"{_format_recent_summaries(recent_summaries)}\n\n"
    )


def _format_story_event(story: Any) -> str:
    if not isinstance(story, dict):
        return ""
    lines = (
        ("Current event", story.get("title")),
        ("Event scene", story.get("scene")),
        ("Event characters", ", ".join(story.get("characters", [])) if isinstance(story.get("characters"), list) else ""),
        ("Event description", story.get("description")),
        ("Completion condition", story.get("completed_when")),
        ("Current progress", story.get("event_progress")),
        ("Next missing beaevent_prt", story.get("next_needed")),
        ("Suggested pressure", story.get("push")),
    )
    return "\n".join(f"- {label}: {value}" for label, value in lines if value not in (None, "", []))


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


def _format_player_snapshot(player: dict[str, Any], location: Any) -> str:
    return "\n".join(
        [
            f"- Profile: {player.get('player_profile', '')}",
            f"- Location: {_format_location(location)}",
            "- Active stat effects:",
            *(_bullet_lines(_active_effects(player.get("stats", {}))) or ["  - none"]),
        ]
    )


def _format_world_snapshot(world: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"- Time: {world.get('time', '')}",
            f"- Weather: {world.get('weather', '')}",
            "- Active world effects:",
            *(_bullet_lines(_active_effects(world.get("stats", {}))) or ["  - none"]),
        ]
    )


def _format_character_snapshots(characters: list[dict[str, Any]]) -> str:
    if not characters:
        return "(none)"
    lines: list[str] = []
    for character in characters:
        lines.append(
            f"- {character.get('name', character.get('id', ''))} ({character.get('id', '')}): "
            f"location={character.get('location', '') or 'unknown'}; "
            f"emotion={character.get('emotion', '') or 'unknown'}"
        )
        lines.extend(f"  - relation effect: {effect}" for effect in character.get("relation_to_player", []) if effect)
    return "\n".join(lines)


def _format_recent_summaries(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        user_input = str(item.get("user_input", "") or "").strip()
        summary = str(item.get("summary", "") or "").strip()
        if user_input or summary:
            lines.append(f"- Player input: {user_input}\n  Summary: {summary}")
    return "\n".join(lines) if lines else "(none)"


def _format_location(location: Any) -> str:
    if isinstance(location, dict):
        name = location.get("name") or location.get("id") or "unknown"
        location_id = location.get("id", "")
        description = location.get("description", "")
        return f"{name} ({location_id}) - {description}" if description else f"{name} ({location_id})"
    return str(location or "unknown")


def _bullet_lines(items: list[str]) -> list[str]:
    return [f"  - {item}" for item in items if str(item).strip()]
