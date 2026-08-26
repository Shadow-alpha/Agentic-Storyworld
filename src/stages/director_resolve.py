from __future__ import annotations

from typing import Any


SYSTEM_PROMPT = '''You are the Director Agent responsible for resolving system progression after the narrative has been written.

Your job is to update world/player state, evaluate story progress, and provide next interaction options.

You will receive:
- CURRENT PLAYER INPUT
- PLAYER and WORLD STATE TO BE UPDATED
- NARRATIVE RESULT: time, scene, narrative
- RECENT TURN SUMMARIES

## State Update

Update only numeric stats value listed in STATE TO BE UPDATED.

Rules:
- Do not repeat unchanged fields or full state objects.
- Do not introduce undefined fields.
- Use absolute final values, not relative changes.
- Each line must be: field = absolute_value | short reason.
- The reason must be based on NARRATIVE RESULT.

If no stat changes, output empty <state_update></state_update>.

{story_update_prompt}

## Options

Provide 3-4 meaningful next options.

Rules:
- Options must be diverse; do not provide the same intent in different words.
- Options should reflect current narrative and story progress.
- Each option should open a distinct next direction. When possible, cover different action types, including public action, location change, investigation, resource/training, test/challenge, or third-party contact.
- At least one option should open a new scene, story direction, or external faction/character.
- Each option id must be a short Chinese label within 8 characters, summarizing the content inside <option>.

## Output Format and Example

<state_update>
world.xxx = 43 | short reason
player.reputation = 45 | short reason
...
</state_update>

{story_update_format}

<interaction>
<option id="追问线索">完整选项内容</option>
<option id="...">...</option>
</interaction>
'''

STORY_UPDATE_PROMPT = '''
## Story Update

Evaluate the current story node from NARRATIVE RESULT and RECENT TURN SUMMARIES.
The story node describes a world event, which is a reference, NOT happened fact.

Current story node:
{story_node}

Rules:
- Mark `status=completed` when the event's `complete_when` condition has been substantially satisfied by confirmed narrative facts or hidden facts.
- Keep it `in_progress` when the event has not yet meaningfully happened or ended.
- event_progress is the accumulated confirmed progress of this event by the end of this turn. If nothing has advanced, keep the previous progress and say what is still unchanged.
- next_needed is the next concrete missing beat needed to advance the event. Do not assume the player. Leave it empty only when status is completed.
- evidence should be brief evidence from this turn's narrative.
'''

STORY_UPDATE_FORMAT = '''<story_update status="in_progress|completed">
event_progress: accumulated confirmed progress by the end of this turn
next_needed: next missing story beat, empty if completed
evidence: brief evidence from this turn
</story_update>'''

def build_system_prompt(state: dict[str, Any]) -> str:
    story = state.get("story", {})
    story_active = isinstance(story, dict) and story.get("mode") in {"pressure", "closure"}
    story_update_prompt = (
        STORY_UPDATE_PROMPT.format(story_node=_format_story_node(story))
        if story_active
        else ""
    )
    story_update_format = STORY_UPDATE_FORMAT if story_active else ""
    return SYSTEM_PROMPT.format(
        story_update_prompt=story_update_prompt,
        story_update_format=story_update_format,
    )

def build_user_prompt(
    narrative_result: dict[str, Any],
    state: dict[str, Any],
    logs: dict[str, Any],
    user_input: Any,
) -> str:
    player = state.get("player", {})
    world = state.get("world", {})
    state_to_be_updated = {
        "player": _stat_update_candidates(player.get("stats", {})),
        "world": _stat_update_candidates(world.get("stats", {})),
    }
    recent_summaries = [
        {
            "turn": record.get("turn_index", index + 1),
            "summary": record.get("director_narrative", {}).get("summary", ""),
        }
        for index, record in enumerate(logs.get("turn_log", [])[-5:])
        if record.get("director_narrative", {}).get("summary", "")
    ]
    story_active = isinstance(state.get("story", {}), dict) and state.get("story", {}).get("mode") in {"pressure", "closure"}
    expected_tags = "state_update, story_update, and interaction" if story_active else "state_update and interaction"
    return (
        "Current player input:\n"
        f"{_format_player_input(user_input)}\n\n"
        "State to be updated:\n"
        f"{_format_state_to_update(state_to_be_updated)}\n\n"
        "Narrative result:\n"
        f"{_narrative_text(narrative_result)}\n\n"
        "Recent turn summaries:\n"
        f"{_format_recent_summaries(recent_summaries)}\n\n"
        f"Return strict TAG format with {expected_tags}."
    )


def _stat_update_candidates(stats: Any) -> dict[str, Any]:
    if not isinstance(stats, dict):
        return {}
    candidates: dict[str, Any] = {}
    for key, value in stats.items():
        if not isinstance(value, dict):
            continue
        candidates[key] = {
            field: value.get(field, "")
            for field in ("value", "description", "update_guidance")
            if field in value
        }
    return candidates


def _format_story_node(story: Any) -> str:
    if not isinstance(story, dict):
        return ""
    lines = (
        ("Current event", story.get("title")),
        ("Event description", story.get("description")),
        ("Current status", story.get("status")),
        ("Completion condition", story.get("completed_when")),
        ("Current progress", story.get("event_progress")),
        ("Next missing beat", story.get("next_needed")),
    )
    return "\n".join(f"- {label}: {value}" for label, value in lines if value not in (None, "", []))


def _narrative_text(narrative_result: Any) -> str:
    if isinstance(narrative_result, dict):
        narrative = narrative_result.get("narrative", narrative_result)
        visible = ""
        if isinstance(narrative, dict):
            visible = str(narrative.get("visible", "") or "")
        else:
            visible = str(narrative or "")
        hidden = narrative_result.get("hidden", {})
        hidden_text = hidden.get("text", "") if isinstance(hidden, dict) else hidden
        hidden_text = str(hidden_text or "").strip()
        if hidden_text:
            scene_id = hidden.get("scene_id", "") if isinstance(hidden, dict) else ""
            return f"Visible narrative:\n{visible}\n\nHidden off-screen story facts ({scene_id}):\n{hidden_text}"
        return visible
    return str(narrative_result or "")


def _format_player_input(user_input: Any) -> str:
    if not isinstance(user_input, dict):
        return str(user_input or "")
    lines = []
    for label, key in (("Intent", "intent"), ("Action", "action")):
        value = str(user_input.get(key, "") or "").strip()
        if value:
            lines.append(f"- {label}: {value}")
    speech = user_input.get("speech", {})
    text = speech.get("text", "") if isinstance(speech, dict) else speech
    text = str(text or "").strip()
    if text:
        audience = speech.get("audience", []) if isinstance(speech, dict) else []
        target = f" to {', '.join(audience)}" if isinstance(audience, list) and audience else ""
        lines.append(f"- Speech{target}: {text}")
    return "\n".join(lines) if lines else "(none)"


def _format_state_to_update(state_to_be_updated: dict[str, Any]) -> str:
    lines: list[str] = []
    for scope, stats in state_to_be_updated.items():
        if not isinstance(stats, dict) or not stats:
            continue
        lines.append(f"{scope}:")
        for key, item in stats.items():
            if not isinstance(item, dict):
                continue
            lines.append(f"- {key}: value={item.get('value', '')}")
            if item.get("description"):
                lines.append(f"  description: {item['description']}")
            if item.get("update_guidance"):
                lines.append(f"  update_guidance: {item['update_guidance']}")
    return "\n".join(lines) if lines else "(none)"


def _format_recent_summaries(items: list[dict[str, Any]]) -> str:
    lines = [
        f"- Turn {item.get('turn', '?')}: {item.get('summary', '')}"
        for item in items
        if item.get("summary")
    ]
    return "\n".join(lines) if lines else "(none)"
