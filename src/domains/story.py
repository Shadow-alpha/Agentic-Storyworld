from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .base import DomainContext


class StoryDomain:
    name = "story"

    def get_agent_view(self, state_view: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        runtime_story = state_view.get("story", {})
        current_id = runtime_story.get("current", "") if isinstance(runtime_story, dict) else ""
        node = self._nodes(context).get(current_id, {})
        runtime_story = runtime_story if isinstance(runtime_story, dict) else {}
        pace = node.get("pace", {}) if isinstance(node, dict) else {}
        turns_in_node = int(runtime_story.get("turns_in_node", 0) or 0)
        world_time = state_view.get("world", {}).get("time", "")
        event_started = self._event_started(pace, world_time)
        time_skip = self._time_skip_view(pace, world_time, turns_in_node)
        turns_since_started = int(runtime_story.get("turns_since_started", 0) or 0) if event_started else 0
        elapsed_since_started = int(runtime_story.get("elapsed_minutes_since_started", 0) or 0) if event_started else 0
        should_close = event_started and self._should_close_event(pace, turns_since_started, elapsed_since_started)
        must_close = event_started and self._must_close_event(pace, turns_since_started, elapsed_since_started)
        mode = "closure" if should_close or must_close else "pressure" if event_started else "time_skip" if time_skip else "none"
        max_elapsed_minutes = self._max_elapsed_minutes(
            pace,
            world_time,
            event_started,
            should_close or must_close,
            elapsed_since_started,
        )
        state_view["story"] = {
            "current": current_id,
            "title": node.get("title", ""),
            "scene": node.get("scene", ""),
            "characters": deepcopy(node.get("characters", [])) if isinstance(node.get("characters"), list) else [],
            "completed_when": node.get("complete_when", node.get("completed_when", "")),
            "pace": deepcopy(pace) if isinstance(pace, dict) else {},
            "turns_in_node": turns_in_node,
            "turns_since_started": turns_since_started,
            "elapsed_minutes_since_started": elapsed_since_started,
            "next": deepcopy(node.get("next", [])) if isinstance(node.get("next"), list) else [],
            "status": "in_progress" if event_started else runtime_story.get("status", "unstarted"),
            "completed": list(runtime_story.get("completed", [])) if isinstance(runtime_story.get("completed"), list) else [],
            "must_close": must_close,
            "mode": mode,
            "max_elapsed_minutes": max_elapsed_minutes,
            "event_progress": runtime_story.get("event_progress", ""),
            "next_needed": runtime_story.get("next_needed", ""),
        }
        if not event_started:
            state_view["story"]["minutes_until_start"] = self._minutes_until_start(pace, world_time)
            state_view["story"]["turns_until_start"] = self._turns_until_start(pace, turns_in_node)
        if time_skip:
            state_view["story"]["time_skip"] = time_skip
        if event_started:
            state_view["story"]["description"] = node.get("description", "")
            state_view["story"]["push"] = node.get("push", "")
        return state_view

    def get_ui_view(self, runtime_state: Any, context: DomainContext) -> dict[str, Any]:
        runtime_state = runtime_state if isinstance(runtime_state, dict) else {}
        runtime_story = runtime_state.get("story", {}) if isinstance(runtime_state.get("story"), dict) else {}
        view = self.get_agent_view(
            {
                "story": deepcopy(runtime_story),
                "world": deepcopy(runtime_state.get("world", {})),
            },
            context,
        ).get("story", {})
        view["nodes"] = self._ui_nodes(runtime_story, context)
        return view

    def apply_update(self, runtime_state: dict[str, Any], turn_record: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        update = turn_record.get("director_resolve", {}).get("story_update", {})
        if not isinstance(update, dict):
            update = {}
        runtime_story = runtime_state.setdefault("story", self.initial_runtime_story(context.static_state.get("story", {})))
        if not isinstance(runtime_story, dict):
            return {}
        runtime_story.pop("elapsed_minutes_in_node", None)
        runtime_story.pop("event_started", None)

        status = self._normalize_status(update.get("status", runtime_story.get("status", "in_progress")))
        text = str(update.get("text", "") or "").strip()
        event_progress = str(update.get("event_progress", "") or "").strip()
        next_needed = str(update.get("next_needed", "") or "").strip()
        current = str(runtime_story.get("current", "") or "")
        node = self._nodes(context).get(current, {})
        pace = node.get("pace", {}) if isinstance(node, dict) else {}
        was_unstarted = runtime_story.get("status", "unstarted") == "unstarted"
        event_active = self._event_started(pace, runtime_state.get("world", {}).get("time", ""))
        event_starting = was_unstarted and event_active
        turns_since_started = int(runtime_story.get("turns_since_started", 0) or 0)
        elapsed_since_started = int(runtime_story.get("elapsed_minutes_since_started", 0) or 0)
        must_close = self._must_close_event(
            pace,
            turns_since_started,
            elapsed_since_started,
        )
        forced = False
        if must_close and status != "completed":
            status = "completed"
            forced = True
        elif event_active and status == "unstarted":
            status = "in_progress"
        elif not event_active and status != "completed":
            status = "unstarted"

        runtime_story["status"] = status
        runtime_story["turns_in_node"] = int(runtime_story.get("turns_in_node", 0) or 0) + 1
        if event_active:
            runtime_story["turns_since_started"] = 0 if event_starting else turns_since_started + 1
            runtime_story["elapsed_minutes_since_started"] = self._elapsed_since_start(
                pace,
                runtime_state.get("world", {}).get("time", ""),
            )
        if text:
            runtime_story["note"] = text
        if event_progress:
            runtime_story["event_progress"] = event_progress
        if next_needed:
            runtime_story["next_needed"] = next_needed
        elif status == "completed":
            runtime_story.pop("next_needed", None)

        story_changes: dict[str, Any] = {}
        if event_starting:
            scene_id = str(node.get("scene", "") or "").strip() if isinstance(node, dict) else ""
            locations = context.static_state.get("world", {}).get("map_locations", {})
            activated_ids = {
                str(item.get("character_id", "") or "").strip()
                for item in turn_record.get("dialogues", [])
                if isinstance(item, dict)
            }
            runtime_characters = runtime_state.setdefault("characters", {})
            for character_id in node.get("characters", []) if isinstance(node, dict) and isinstance(node.get("characters"), list) else []:
                character_id = str(character_id or "").strip()
                if not character_id or character_id in activated_ids:
                    continue
                if not scene_id or not isinstance(locations, dict) or scene_id not in locations:
                    continue
                character = runtime_characters.get(character_id, {})
                character_state = character.get("state", {}) if isinstance(character, dict) else {}
                old_location = character_state.get("location")
                character_state["location"] = scene_id
                character_changes: dict[str, Any] = {}
                context.record_change(
                    character_changes,
                    ("location",),
                    old_location,
                    scene_id,
                    "当前事件开始，未激活角色被调度至事件场景。",
                )
                if character_changes:
                    story_changes[character_id] = character_changes

        next_id = ""
        if status == "completed":
            completed = runtime_story.setdefault("completed", [])
            if current and isinstance(completed, list) and current not in completed:
                completed.append(current)
            next_id = self._next_story_id(current, context)
            if next_id:
                runtime_story["current"] = next_id
                runtime_story["status"] = "unstarted"
                runtime_story["turns_in_node"] = 0
                runtime_story["turns_since_started"] = 0
                runtime_story["elapsed_minutes_since_started"] = 0
                runtime_story.pop("event_progress", None)
                runtime_story.pop("next_needed", None)
            else:
                node = self._nodes(context).get(current, {})
                runtime_story["ending_state"] = {
                    "is_ended": True,
                    "ending_id": f"story:{current}",
                    "title": node.get("title", current) if isinstance(node, dict) else current,
                    "description": text,
                    "priority": 0,
                }

        if not update and not must_close:
            return {"characters": story_changes} if story_changes else {}
        story_update = {
            "story_id": current,
            "status": status,
            "text": text,
            "event_progress": event_progress,
            "next_needed": next_needed,
        }
        if next_id:
            story_update["next_story_id"] = next_id
        if forced:
            story_update["forced"] = True
            story_update["reason"] = "max_pace_reached"
        turn_record.setdefault("director_resolve", {})["story_update"] = story_update
        result: dict[str, Any] = {}
        if story_changes:
            result["characters"] = story_changes
        return result

    def build_event_memory_request(
        self,
        turn_record: dict[str, Any],
        state_before: dict[str, Any],
        story_nodes: dict[str, Any],
    ) -> dict[str, Any]:
        story = state_before.get("story", {}) if isinstance(state_before, dict) else {}
        story_update = turn_record.get("director_resolve", {}).get("story_update", {})
        if not isinstance(story_update, dict):
            return {}
        if story_update.get("status") != "completed" and not story.get("must_close"):
            return {}

        story_id = str(story.get("current", "") or "").strip()
        node = story_nodes.get(story_id, {}) if isinstance(story_nodes, dict) else {}
        if not isinstance(node, dict):
            return {}
        character_ids = [
            str(character_id).strip()
            for character_id in node.get("characters", [])
            if str(character_id).strip()
        ] if isinstance(node.get("characters"), list) else []
        if not character_ids:
            return {}

        event_story_update = dict(story_update)
        if story.get("must_close"):
            event_story_update["status"] = "completed"
        narrative_result = turn_record.get("director_narrative", {})
        narrative = narrative_result.get("narrative", "") if isinstance(narrative_result, dict) else ""
        hidden_block = narrative_result.get("hidden", {}) if isinstance(narrative_result, dict) else {}
        hidden = hidden_block.get("text", "") if isinstance(hidden_block, dict) else str(hidden_block or "")
        return {
            "story_id": story_id,
            "title": node.get("title", story.get("title", story_id)),
            "characters": character_ids,
            "event_progress": event_story_update.get("event_progress", "") or event_story_update.get("text", ""),
            "hidden": hidden,
            "narrative": "" if hidden else str(narrative or ""),
        }

    def initial_runtime_story(self, story_nodes: Any) -> dict[str, Any]:
        nodes = story_nodes if isinstance(story_nodes, dict) else {}
        current = next(
            (node_id for node_id, node in nodes.items() if isinstance(node, dict) and node.get("status") == "in_progress"),
            next(iter(nodes), ""),
        )
        return {
            "current": current,
            "status": "unstarted",
            "turns_in_node": 0,
            "turns_since_started": 0,
            "elapsed_minutes_since_started": 0,
            "completed": [],
        }

    def _nodes(self, context: DomainContext) -> dict[str, Any]:
        nodes = context.static_state.get("story", {})
        return nodes if isinstance(nodes, dict) else {}

    def _ui_nodes(self, runtime_story: dict[str, Any], context: DomainContext) -> list[dict[str, str]]:
        current = str(runtime_story.get("current", "") or "")
        completed = set(runtime_story.get("completed", []) if isinstance(runtime_story.get("completed"), list) else [])
        nodes = []
        for node_id, node in self._nodes(context).items():
            if not isinstance(node, dict):
                continue
            status = "current" if node_id == current else "completed" if node_id in completed else "upcoming"
            nodes.append(
                {
                    "id": str(node_id),
                    "title": str(node.get("title") or node_id),
                    "scene": str(node.get("scene") or ""),
                    "status": status,
                }
            )
        return nodes

    def _event_started(self, pace: Any, world_time: Any) -> bool:
        pace = pace if isinstance(pace, dict) else {}
        start_at = pace.get("start_at")
        if not start_at:
            return True
        current_minutes = self._time_to_minutes(world_time)
        start_minutes = self._time_to_minutes(start_at)
        return current_minutes is not None and start_minutes is not None and current_minutes >= start_minutes

    def _time_skip_view(self, pace: Any, world_time: Any, turns_in_node: int) -> dict[str, Any]:
        pace = pace if isinstance(pace, dict) else {}
        if not self._reached(turns_in_node, pace.get("pre_start_turns")):
            return {}
        current_minutes = self._time_to_minutes(world_time)
        start_minutes = self._time_to_minutes(pace.get("start_at"))
        if current_minutes is None or start_minutes is None or current_minutes >= start_minutes:
            return {}
        return {
            "current_time": str(world_time or ""),
            "next_story_time": str(pace.get("start_at") or ""),
            "minutes_until_start": start_minutes - current_minutes,
        }

    def _minutes_until_start(self, pace: Any, world_time: Any) -> int | None:
        pace = pace if isinstance(pace, dict) else {}
        current_minutes = self._time_to_minutes(world_time)
        start_minutes = self._time_to_minutes(pace.get("start_at"))
        if current_minutes is None or start_minutes is None:
            return None
        return max(0, start_minutes - current_minutes)

    def _turns_until_start(self, pace: Any, turns_in_node: int) -> int | None:
        pace = pace if isinstance(pace, dict) else {}
        if pace.get("pre_start_turns") is None:
            return None
        try:
            return max(0, int(pace.get("pre_start_turns") or 0) - turns_in_node)
        except (TypeError, ValueError):
            return None

    def _max_elapsed_minutes(
        self,
        pace: Any,
        world_time: Any,
        event_started: bool,
        closing: bool,
        elapsed_since_started: int,
    ) -> int:
        pace = pace if isinstance(pace, dict) else {}
        if not event_started:
            current_minutes = self._time_to_minutes(world_time)
            start_minutes = self._time_to_minutes(pace.get("start_at"))
            if current_minutes is not None and start_minutes is not None and start_minutes > current_minutes:
                return start_minutes - current_minutes + 1
        limit = pace.get("max_duration" if closing else "soft_duration")
        try:
            return max(1, int(limit) - elapsed_since_started + 1)
        except (TypeError, ValueError):
            return 1440

    def _should_close_event(self, pace: Any, turns: int, minutes: int) -> bool:
        pace = pace if isinstance(pace, dict) else {}
        return self._reached(turns, pace.get("soft_turns")) or self._reached(minutes, pace.get("soft_duration"))

    def _must_close_event(self, pace: Any, turns: int, minutes: int) -> bool:
        pace = pace if isinstance(pace, dict) else {}
        return self._reached(turns, pace.get("max_turns")) or self._reached(minutes, pace.get("max_duration"))

    def _reached(self, value: int, limit: Any) -> bool:
        try:
            limit = int(limit or 0)
        except (TypeError, ValueError):
            return False
        return limit > 0 and value >= limit

    def _time_to_minutes(self, value: Any) -> int | None:
        match = re.match(r"^\s*Day\s*(\d+)\s+(\d{1,2}):(\d{1,2})\s*$", str(value or ""))
        if not match:
            return None
        day, hour, minute = (int(part) for part in match.groups())
        return (day - 1) * 1440 + hour * 60 + minute

    def _elapsed_since_start(self, pace: Any, world_time: Any) -> int:
        pace = pace if isinstance(pace, dict) else {}
        current_minutes = self._time_to_minutes(world_time)
        start_minutes = self._time_to_minutes(pace.get("start_at"))
        if current_minutes is None or start_minutes is None:
            return 0
        return max(0, current_minutes - start_minutes)

    def _elapsed_minutes(self, turn_record: dict[str, Any]) -> int:
        time = turn_record.get("director_narrative", {}).get("time", {})
        if isinstance(time, dict):
            try:
                return max(0, int(time.get("elapsed_minutes", 0) or 0))
            except (TypeError, ValueError):
                return 0
        return 0

    def _next_story_id(self, current: str, context: DomainContext) -> str:
        node = self._nodes(context).get(current, {})
        next_items = node.get("next", []) if isinstance(node, dict) else []
        return str(next_items[0]) if isinstance(next_items, list) and next_items else ""

    def _normalize_status(self, status: Any) -> str:
        status = str(status or "in_progress").strip().lower()
        return status if status in {"unstarted", "in_progress", "completed"} else "in_progress"
