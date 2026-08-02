from __future__ import annotations

import json
from typing import Any, Iterator

from .prompts import Director_Ending_Prompt, Director_Narrative_Prompt, Director_Plan_Prompt, Director_Resolve_Prompt
from .schemas import INPUT_MODE_HYBRID
from .stream_parsers.xml_protocol import (
    NarrativeStreamParser,
    PlanStreamParser,
    ResolveStreamParser,
    parse_ending_xml,
    parse_narrative_xml,
    parse_plan_xml,
    parse_resolve_xml,
)


class DirectorAgent:
    """Plans character execution and integrates environment feedback."""

    def __init__(self, llm_client=None, thinking: bool = False, state_manager=None) -> None:
        self.llm_client = llm_client
        self.thinking_enabled = thinking
        self.default_input_mode = INPUT_MODE_HYBRID
        self.state_manager = state_manager

    def plan(self, user_input: str, state: dict[str, Any], logs: dict[str, Any]) -> dict[str, Any]:
        state = self._resolve_state(state)
        if self.llm_client:
            try:
                user_prompt = self._build_plan_prompt(user_input, state, logs)
                raw_text = self.llm_client.generate_text(
                    system_prompt=Director_Plan_Prompt,
                    user_prompt=user_prompt,
                )
                self._log_complete_output("director_plan", raw_text)
                return self._normalize_plan(parse_plan_xml(raw_text), state)
            except Exception:
                pass
        return self._normalize_plan(self._build_fallback_plan(user_input, state), state)

    def narrative(
        self,
        env_feedback: dict[str, Any],
        state: dict[str, Any],
        logs: dict[str, Any],
        user_input: Any = "",
    ) -> dict[str, Any]:
        state = self._resolve_state(state)
        if self.llm_client:
            try:
                user_prompt = self._build_narrative_prompt(env_feedback, state, logs, user_input)
                raw_text = self.llm_client.generate_text(
                    system_prompt=Director_Narrative_Prompt,
                    user_prompt=user_prompt,
                )
                self._log_complete_output("director_narrative", raw_text)
                return self._normalize_narrative(parse_narrative_xml(raw_text), state)
            except Exception:
                pass
        return self._normalize_narrative(self._build_fallback_narrative(env_feedback, state), state)

    def resolve(
        self,
        narrative_result: dict[str, Any],
        state: dict[str, Any],
        logs: dict[str, Any],
        user_input: Any = "",
    ) -> dict[str, Any]:
        state = self._resolve_state(state)
        if self.llm_client:
            try:
                user_prompt = self._build_resolve_prompt(narrative_result, state, logs, user_input)
                raw_text = self.llm_client.generate_text(
                    system_prompt=Director_Resolve_Prompt,
                    user_prompt=user_prompt,
                )
                self._log_complete_output("director_resolve", raw_text)
                return self._normalize_resolve(parse_resolve_xml(raw_text))
            except Exception:
                pass
        return self._normalize_resolve(self._build_fallback_resolve(narrative_result))

    def ending(
        self,
        ending: dict[str, Any],
        narrative_result: dict[str, Any],
        state: dict[str, Any],
        logs: dict[str, Any],
        env_feedback: dict[str, Any],
    ) -> dict[str, str]:
        state = self._resolve_state(state)
        if self.llm_client:
            try:
                user_prompt = self._build_ending_prompt(ending, narrative_result, state, logs, env_feedback)
                raw_text = self.llm_client.generate_text(
                    system_prompt=Director_Ending_Prompt,
                    user_prompt=user_prompt,
                )
                self._log_complete_output("director_ending", raw_text)
                return self._normalize_ending(parse_ending_xml(raw_text), ending)
            except Exception:
                pass
        return self._normalize_ending(self._build_fallback_ending(ending, narrative_result), ending)

    def stream_plan(
        self,
        user_input: str,
        state: dict[str, Any],
        logs: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        state = self._resolve_state(state)
        yield {"event": "stage_started", "data": {"stage": "director_plan"}}
        if not self.llm_client:
            plan = self._normalize_plan(self._build_fallback_plan(user_input, state), state)
            for index, character in enumerate(plan.get("characters", [])):
                yield {
                    "event": "block_started",
                    "data": {
                        "stage": "director_plan",
                        "block": "character",
                        "block_index": index,
                        "attrs": {"id": character.get("id", "")},
                    },
                }
                yield {
                    "event": "block_done",
                    "data": {
                        "stage": "director_plan",
                        "block": "character",
                        "block_index": index,
                        "attrs": {"id": character.get("id", "")},
                        "parsed": character,
                    },
                }
            yield {"event": "stage_done", "data": {"stage": "director_plan", "raw_text": "", "plan": plan}}
            return

        parser = PlanStreamParser(thinking=self.thinking_enabled)
        raw_parts: list[str] = []
        accumulated_plan: dict[str, Any] = {"characters": [], "director_meta": {}}
        user_prompt = self._build_plan_prompt(user_input, state, logs)
        for delta in self.llm_client.stream_text(
            system_prompt=Director_Plan_Prompt,
            user_prompt=user_prompt,
        ):
            if not delta:
                continue
            raw_parts.append(delta)
            for parsed_event in parser.feed(delta):
                event_type = parsed_event["type"]
                if event_type in {"thinking_started", "thinking_delta", "thinking_done"}:
                    yield {
                        "event": event_type,
                        "data": {
                            "source": "director_plan",
                            "delta": parsed_event.get("delta", ""),
                            "text": parsed_event.get("text", ""),
                        },
                    }
                elif event_type in {"block_started", "block_delta", "block_done"}:
                    parsed = parsed_event.get("parsed")
                    if event_type == "block_done" and parsed_event.get("block") == "character":
                        partial_plan = parse_plan_xml("".join(raw_parts))
                        parsed = {
                            "player_input": partial_plan.get("player_input", {}),
                            "context": partial_plan.get("context", ""),
                            **(parsed or {}),
                        }
                        parsed = self._enrich_character(parsed or {}, state)
                        character_index = parsed_event.get("block_index", 0)
                        while len(accumulated_plan["characters"]) <= character_index:
                            accumulated_plan["characters"].append({})
                        accumulated_plan["characters"][character_index] = parsed
                    yield {
                        "event": event_type,
                        "data": {
                            "stage": "director_plan",
                            "block": parsed_event.get("block", ""),
                            "block_index": parsed_event.get("block_index", 0),
                            "attrs": parsed_event.get("attrs", {}),
                            "delta": parsed_event.get("delta", ""),
                            "text": parsed_event.get("text", ""),
                            "parsed": parsed,
                            "display_text": parsed_event.get("display_text", ""),
                            "parsed_partial": parsed_event.get("parsed_partial"),
                        },
                    }

        raw_text = "".join(raw_parts)
        if raw_text:
            self._log_complete_output("director_plan", raw_text)
        parsed_plan = parse_plan_xml(raw_text) if raw_text else {}
        plan = {
            "player_input": parsed_plan.get("player_input", {}),
            "context": parsed_plan.get("context", ""),
            "characters": [character for character in accumulated_plan.get("characters", []) if character.get("id")],
            "director_meta": accumulated_plan.get("director_meta", {}),
        }
        if raw_text and not plan["characters"]:
            plan = self._normalize_plan(parsed_plan, state)
        elif raw_text:
            plan = self._normalize_plan(plan, state)
        elif not raw_text:
            plan = self._normalize_plan(self._build_fallback_plan(user_input, state), state)
        yield {"event": "stage_done", "data": {"stage": "director_plan", "raw_text": raw_text, "plan": plan}}

    def stream_narrative(
        self,
        env_feedback: dict[str, Any],
        state: dict[str, Any],
        logs: dict[str, Any],
        user_input: Any = "",
    ) -> Iterator[dict[str, Any]]:
        state = self._resolve_state(state)
        yield {"event": "stage_started", "data": {"stage": "director_narrative"}}
        if not self.llm_client:
            result = self._normalize_narrative(self._build_fallback_narrative(env_feedback, state), state)
            for index, block_name in enumerate(("time", "scene", "narrative", "summary")):
                yield {
                    "event": "block_done",
                    "data": {
                        "stage": "director_narrative",
                        "block": block_name,
                        "block_index": index,
                        "parsed": result.get(block_name),
                    },
                }
            yield {"event": "stage_done", "data": {"stage": "director_narrative", "raw_text": "", "narrative_result": result}}
            return

        parser = NarrativeStreamParser(thinking=self.thinking_enabled)
        raw_parts: list[str] = []
        accumulated_result = {
            "time": "",
            "scene": "",
            "narrative": {"visible": "", "hidden": ""},
            "summary": "",
        }
        user_prompt = self._build_narrative_prompt(env_feedback, state, logs, user_input)
        for delta in self.llm_client.stream_text(
            system_prompt=Director_Narrative_Prompt,
            user_prompt=user_prompt,
        ):
            if not delta:
                continue
            raw_parts.append(delta)
            for parsed_event in parser.feed(delta):
                event_type = parsed_event["type"]
                if event_type in {"thinking_started", "thinking_delta", "thinking_done"}:
                    yield {
                        "event": event_type,
                        "data": {
                            "source": "director_narrative",
                            "delta": parsed_event.get("delta", ""),
                            "text": parsed_event.get("text", ""),
                        },
                    }
                elif event_type in {"block_started", "block_delta", "block_done"}:
                    parsed = parsed_event.get("parsed")
                    if event_type == "block_done":
                        block_name = parsed_event.get("block", "")
                        if block_name:
                            accumulated_result[block_name] = parsed
                        parsed = accumulated_result.get(block_name, parsed)
                    yield {
                        "event": event_type,
                        "data": {
                            "stage": "director_narrative",
                            "block": parsed_event.get("block", ""),
                            "block_index": parsed_event.get("block_index", 0),
                            "attrs": parsed_event.get("attrs", {}),
                            "delta": parsed_event.get("delta", ""),
                            "text": parsed_event.get("text", ""),
                            "parsed": parsed,
                            "display_text": parsed_event.get("display_text", ""),
                            "parsed_partial": parsed_event.get("parsed_partial"),
                        },
                    }

        raw_text = "".join(raw_parts)
        if raw_text:
            self._log_complete_output("director_narrative", raw_text)
        result = self._normalize_narrative(accumulated_result if raw_text else self._build_fallback_narrative(env_feedback, state), state)
        if raw_text and not result.get("narrative", {}).get("visible"):
            result = self._normalize_narrative(parse_narrative_xml(raw_text), state)
        yield {"event": "stage_done", "data": {"stage": "director_narrative", "raw_text": raw_text, "narrative_result": result}}

    def stream_resolve(
        self,
        narrative_result: dict[str, Any],
        state: dict[str, Any],
        logs: dict[str, Any],
        user_input: Any = "",
    ) -> Iterator[dict[str, Any]]:
        state = self._resolve_state(state)
        yield {"event": "stage_started", "data": {"stage": "director_resolve"}}
        if not self.llm_client:
            result = self._normalize_resolve(self._build_fallback_resolve(narrative_result))
            for index, block_name in enumerate(("state_update", "goal_update", "interaction")):
                yield {
                    "event": "block_done",
                    "data": {
                        "stage": "director_resolve",
                        "block": block_name,
                        "block_index": index,
                        "parsed": result.get(block_name),
                    },
                }
            yield {"event": "stage_done", "data": {"stage": "director_resolve", "raw_text": "", "resolve_result": result}}
            return

        parser = ResolveStreamParser(thinking=self.thinking_enabled)
        raw_parts: list[str] = []
        accumulated_result = {
            "goal_update": {"checkpoints": []},
            "interaction": {"mode": INPUT_MODE_HYBRID, "options": []},
            "state_update": {"world_state": {}, "user_state": {}},
        }
        user_prompt = self._build_resolve_prompt(narrative_result, state, logs, user_input)
        for delta in self.llm_client.stream_text(
            system_prompt=Director_Resolve_Prompt,
            user_prompt=user_prompt,
        ):
            if not delta:
                continue
            raw_parts.append(delta)
            for parsed_event in parser.feed(delta):
                event_type = parsed_event["type"]
                if event_type in {"thinking_started", "thinking_delta", "thinking_done"}:
                    yield {
                        "event": event_type,
                        "data": {
                            "source": "director_resolve",
                            "delta": parsed_event.get("delta", ""),
                            "text": parsed_event.get("text", ""),
                        },
                    }
                elif event_type in {"block_started", "block_delta", "block_done"}:
                    parsed = parsed_event.get("parsed")
                    if event_type == "block_done":
                        block_name = parsed_event.get("block", "")
                        if block_name == "goal_update":
                            accumulated_result["goal_update"] = self._normalize_goal_update(parsed or {})
                        elif block_name == "state_update":
                            accumulated_result["state_update"] = self._normalize_state_update(parsed or {})
                        elif block_name == "interaction":
                            accumulated_result["interaction"] = {
                                "mode": INPUT_MODE_HYBRID,
                                "options": (parsed or {}).get("options", []) if isinstance(parsed, dict) else [],
                            }
                        elif block_name:
                            accumulated_result[block_name] = parsed
                        parsed = accumulated_result.get(block_name, parsed)
                    yield {
                        "event": event_type,
                        "data": {
                            "stage": "director_resolve",
                            "block": parsed_event.get("block", ""),
                            "block_index": parsed_event.get("block_index", 0),
                            "attrs": parsed_event.get("attrs", {}),
                            "delta": parsed_event.get("delta", ""),
                            "text": parsed_event.get("text", ""),
                            "parsed": parsed,
                            "display_text": parsed_event.get("display_text", ""),
                            "parsed_partial": parsed_event.get("parsed_partial"),
                        },
                    }

        raw_text = "".join(raw_parts)
        if raw_text:
            self._log_complete_output("director_resolve", raw_text)
        result = accumulated_result
        if not raw_text:
            result = self._normalize_resolve(self._build_fallback_resolve(narrative_result))
        yield {"event": "stage_done", "data": {"stage": "director_resolve", "raw_text": raw_text, "resolve_result": result}}

    def _build_plan_prompt(self, user_input: str, state: dict[str, Any], logs: dict[str, Any]) -> str:
        user_state = state.get("user_state", {})
        world_state = state.get("world_state", {})
        current_location = user_state.get("location")
        map_locations = world_state.get("map_locations", {})
        current_location_id = current_location.get("id", "") if isinstance(current_location, dict) else current_location
        current_location_info = (
            current_location
            if isinstance(current_location, dict)
            else map_locations.get(current_location_id, {}) if isinstance(map_locations, dict) else {}
        )
        player_stats = self._active_effects(user_state.get("stats", {}))
        player_snapshot = {
            "player_profile": user_state.get("player_profile", ""),
            "location": current_location_info,
            "stats": player_stats,
        }
        local_world_snapshot = {
            "time": world_state.get("time", ""),
            "weather": world_state.get("weather", ""),
            "stats": self._active_effects(world_state.get("stats", {})),
        }
        character_snapshots = []
        for character_id, character in state.get("characters", {}).items():
            character_state = character.get("state", {})
            relations = character_state.get("relations", {})
            character_location = character_state.get("location")

            character_snapshots.append(
                {
                    "id": character_id,
                    "name": character_state.get("name", character_id),
                    # "aliases": character_state.get("aliases", []),
                    "location": (
                        character_location.get("name", "")
                        if isinstance(character_location, dict)
                        else map_locations.get(character_location, {}).get("name", "") if isinstance(map_locations, dict) else ""
                    ),
                    "emotion": character_state.get("emotion", ""),
                    "relation_to_player": self._active_effects(relations),
                }
            )
        recent_summaries = [
            {
                "user_input": record.get("user_input", {}).get("raw_text")
                or record.get("user_input", {}).get("selected_choice", ""),
                "summary": record.get("director_result", {}).get("summary", ""),
            }
            for record in logs.get("turn_log", [])[-5:]
        ]
        return (
            "Current player input:\n"
            f"{self._dump_prompt_json(user_input)}\n\n"
            "Player snapshot:\n"
            f"{player_snapshot}\n\n"
            "Local world snapshot:\n"
            f"{local_world_snapshot}\n\n"
            "Character snapshots:\n"
            f"{character_snapshots}\n\n"
            "Active goals snapshot:\n"
            f"{state.get('goals', {})}\n\n"
            "Recent turn summaries:\n"
            f"{recent_summaries}\n\n"
            # "Return strict XML matching the required <plans><character .../></plans> schema."
        )

    def _build_narrative_prompt(
        self,
        env_feedback: dict[str, Any],
        state: dict[str, Any],
        logs: dict[str, Any],
        user_input: Any,
    ) -> str:
        user_state = state.get("user_state", {})
        world_state = state.get("world_state", {})
        player_state = {
            "player_profile": user_state.get("player_profile", ""),
            "location": user_state.get("location", {}),
            "stats": self._active_effects(user_state.get("stats", {})),
            "possessions": user_state.get("possessions", []),
        }
        local_world_state = {
            "time": world_state.get("time", ""),
            "weather": world_state.get("weather", ""),
            "stats": self._active_effects(world_state.get("stats", {})),
        }
        map_locations = world_state.get("map_locations", {})
        known_scene_ids = {
            scene_id: scene.get("name", scene_id)
            for scene_id, scene in map_locations.items()
            if isinstance(scene, dict)
        } if isinstance(map_locations, dict) else {}
        character_dialogue = [
            {
                "character_id": feedback.get("character_id", ""),
                "name": feedback.get("name", feedback.get("character_id", "")),
                "order": feedback.get("order", 1),
                "raw_response": feedback.get("raw_response") or feedback.get("response", ""),
            }
            for feedback in env_feedback.get("character_feedback", [])
            if isinstance(feedback, dict)
        ]
        recent_summaries = [
            {
                "turn": record.get("turn_index", index + 1),
                "summary": record.get("director_result", {}).get("summary", ""),
            }
            for index, record in enumerate(logs.get("turn_log", [])[-5:])
            if record.get("director_result", {}).get("summary", "")
        ]
        return (
            "Current player input:\n"
            f"{self._dump_prompt_json(user_input)}\n\n"
            "Player state before this turn:\n"
            f"{self._dump_prompt_json(player_state)}\n\n"
            "Local world state before this turn:\n"
            f"{self._dump_prompt_json(local_world_state)}\n\n"
            "Known scene ids:\n"
            f"{self._dump_prompt_json(known_scene_ids)}\n\n"
            "Character dialogue this turn:\n"
            f"{self._dump_prompt_json(character_dialogue)}\n\n"
            "Recent turn summaries:\n"
            f"{self._dump_prompt_json(recent_summaries)}\n\n"
            "Return strict TAG format with time, scene, narrative, and summary."
        )

    def _build_resolve_prompt(
        self,
        narrative_result: dict[str, Any],
        state: dict[str, Any],
        logs: dict[str, Any],
        user_input: Any,
    ) -> str:
        user_state = state.get("user_state", {})
        world_state = state.get("world_state", {})
        state_to_be_updated = {
            "player": self._stat_update_candidates(user_state.get("stats", {})),
            "world": self._stat_update_candidates(world_state.get("stats", {})),
        }
        recent_summaries = [
            {
                "turn": record.get("turn_index", index + 1),
                "summary": record.get("director_result", {}).get("summary", ""),
            }
            for index, record in enumerate(logs.get("turn_log", [])[-5:])
            if record.get("director_result", {}).get("summary", "")
        ]
        return (
            "Current player input:\n"
            f"{user_input}\n\n"
            "State to be updated:\n"
            f"{self._dump_prompt_json(state_to_be_updated)}\n\n"
            "Active goals and checkpoints:\n"
            f"{self._dump_prompt_json(state.get('goals', {}))}\n\n"
            "Narrative result:\n"
            f"{self._narrative_text(narrative_result)}\n\n"
            "Recent turn summaries:\n"
            f"{self._dump_prompt_json(recent_summaries)}\n\n"
            "Return strict TAG format with state_update, goal_update, and interaction."
        )

    def _build_ending_prompt(
        self,
        ending: dict[str, Any],
        narrative_result: dict[str, Any],
        state: dict[str, Any],
        logs: dict[str, Any],
        env_feedback: dict[str, Any],
    ) -> str:
        recent_summaries = self._recent_summaries(logs)
        runtime_goals = self.state_manager.get_runtime_state().get("goals", {}) if self.state_manager else {}
        completed_goals = runtime_goals.get("completed_goals", []) if isinstance(runtime_goals, dict) else []
        return (
            "Selected ending:\n"
            f"{self._dump_prompt_json(ending)}\n\n"
            "Final turn narrative:\n"
            f"{self._narrative_text(narrative_result)}\n\n"
            "Completed goals:\n"
            f"{self._dump_prompt_json(completed_goals)}\n\n"
            "Player and world state:\n"
            f"{self._dump_prompt_json({'player': state.get('user_state', {}), 'world': state.get('world_state', {})})}\n\n"
            "Character outcomes this turn:\n"
            f"{self._dump_prompt_json(env_feedback.get('character_feedback', []))}\n\n"
            "Recent turn summaries:\n"
            f"{self._dump_prompt_json(recent_summaries)}\n\n"
            "Return only <ending_narrative>."
        )

    def _build_fallback_plan(self, user_input: str, state: dict[str, Any]) -> dict[str, Any]:
        characters = list(state.get("characters", {}).keys())
        raw_text = user_input or "继续"
        intent = "question" if "?" in raw_text or "？" in raw_text else "talk"
        player_input = {
            "intent": intent,
            "action": raw_text,
            "speech": raw_text if intent == "question" else "",
        }
        planned_characters = []
        if characters:
            planned_characters.append(
                {
                    "id": characters[0],
                    "order": 1,
                    "player_input": player_input,
                    "context": f"用户刚刚对你说：{raw_text}",
                }
            )
        return {
            "player_input": player_input,
            "context": raw_text,
            "characters": planned_characters,
            "director_meta": {"reasoning": "fallback planner"},
        }

    def _build_fallback_narrative(self, env_feedback: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        visible_parts = [
            feedback.get("raw_response") or feedback.get("response", "")
            for feedback in env_feedback.get("character_feedback", [])
            if isinstance(feedback, dict)
        ]
        visible = "\n".join(part for part in visible_parts if part).strip()
        if not visible:
            visible = env_feedback.get("env_summary") or "周围暂时没有新的变化。"
        world_state = state.get("world_state", {})
        location = state.get("user_state", {}).get("location", {})
        scene = location.get("name", "") if isinstance(location, dict) else str(location or "")
        return {
            "time": world_state.get("time", ""),
            "scene": scene,
            "narrative": {"visible": visible, "hidden": ""},
            "summary": visible,
        }

    def _build_fallback_resolve(self, narrative_result: dict[str, Any]) -> dict[str, Any]:
        narrative = narrative_result.get("narrative", {})
        visible = narrative.get("visible", "") if isinstance(narrative, dict) else str(narrative or "")
        return {
            "state_update": {
                "world_state": {},
                "user_state": {},
            },
            "goal_update": {"checkpoints": []},
            "interaction": {
                "mode": INPUT_MODE_HYBRID,
                "options": self._build_choices(visible),
            },
        }

    def _build_fallback_ending(self, ending: dict[str, Any], narrative_result: dict[str, Any]) -> dict[str, str]:
        ending_text = ending.get("description", "") or self._narrative_text(narrative_result)
        return {"narrative": ending_text}

    def _build_choices(self, context: str = "") -> list[dict[str, str]]:
        return [
            {"id": "explore", "text": "继续探索周围环境"},
            {"id": "wait", "text": "稍作等待"},
            {"id": "reflect", "text": "整理目前获得的信息"},
        ]

    def _normalize_plan(self, plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        player_input = self._normalize_player_input(plan.get("player_input", {}))
        context = plan.get("context", "")
        characters = []
        for character in plan.get("characters", []):
            if not isinstance(character, dict) or not character.get("id"):
                continue
            enriched = self._enrich_character(character, state)
            if player_input and not enriched.get("player_input"):
                enriched["player_input"] = player_input
            if context and not enriched.get("context"):
                enriched["context"] = context
            characters.append(enriched)
        return {
            "player_input": player_input,
            "context": context,
            "characters": characters,
            "director_meta": plan.get("director_meta", {}),
        }

    def _normalize_narrative(self, result: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        narrative = result.get("narrative", {"visible": "", "hidden": ""})
        if isinstance(narrative, str):
            narrative = {"visible": narrative, "hidden": ""}
        if not isinstance(narrative, dict):
            narrative = {"visible": "", "hidden": ""}
        scene = result.get("scene", {})
        if isinstance(scene, str):
            scene = {"id": "", "name": scene}
        if not isinstance(scene, dict):
            scene = {"id": "", "name": ""}
        return {
            "time": result.get("time", ""),
            "scene": {
                "id": scene.get("id", ""),
                "name": scene.get("name", ""),
            },
            "narrative": {
                "visible": narrative.get("visible", ""),
                "hidden": narrative.get("hidden", ""),
            },
            "summary": result.get("summary", ""),
        }

    def _normalize_resolve(self, result: dict[str, Any]) -> dict[str, Any]:
        interaction = result.get("interaction", {})
        return {
            "state_update": self._normalize_state_update(result.get("state_update", {})),
            "goal_update": self._normalize_goal_update(result.get("goal_update", {})),
            "interaction": {
                "mode": INPUT_MODE_HYBRID,
                "options": interaction.get("options", []) if isinstance(interaction, dict) else [],
            },
        }

    def _normalize_ending(self, result: dict[str, str], ending: dict[str, Any]) -> dict[str, str]:
        narrative = str(result.get("narrative") or "").strip()
        if not narrative:
            narrative = str(ending.get("description") or "").strip()
        return {"narrative": narrative}

    def _normalize_goal_update(self, goal_update: dict[str, Any]) -> dict[str, Any]:
        checkpoints = []
        if not isinstance(goal_update, dict):
            return {"checkpoints": checkpoints}

        seen = set()
        for item in goal_update.get("checkpoints", []):
            if not isinstance(item, dict):
                continue
            goal_id = str(item.get("goal_id", "")).strip()
            checkpoint_id = str(item.get("checkpoint_id", "")).strip()
            if not goal_id or not checkpoint_id:
                continue
            status = self._normalize_checkpoint_status(item.get("status", "in_progress"))
            progress_note = str(item.get("progress_note") or item.get("evidence") or "").strip()
            key = (goal_id, checkpoint_id, status)
            if key in seen:
                continue
            seen.add(key)
            checkpoints.append(
                {
                    "goal_id": goal_id,
                    "checkpoint_id": checkpoint_id,
                    "status": status,
                    "progress_note": progress_note,
                }
            )
        return {"checkpoints": checkpoints}

    def _normalize_checkpoint_status(self, status: Any) -> str:
        normalized = str(status or "in_progress").strip().lower()
        return normalized if normalized in {"unstarted", "available", "in_progress", "completed"} else "in_progress"

    def _normalize_state_update(self, state_update: dict[str, Any]) -> dict[str, Any]:
        return {
            "world_state": state_update.get("world_state", {}),
            "user_state": state_update.get("user_state", {}),
        }

    def _character_display_name(self, state: dict[str, Any], character_id: str) -> str:
        return state.get("characters", {}).get(character_id, {}).get("state", {}).get("name", character_id)

    def _resolve_state(self, fallback_state: dict[str, Any]) -> dict[str, Any]:
        if self.state_manager is not None:
            return self.state_manager.get_agent_state_view()
        return fallback_state

    def _dump_prompt_json(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    def _stat_update_candidates(self, stats: Any) -> dict[str, Any]:
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

    def _narrative_text(self, narrative_result: Any) -> str:
        if isinstance(narrative_result, dict):
            narrative = narrative_result.get("narrative", narrative_result)
            if isinstance(narrative, dict):
                return str(narrative.get("visible", "") or "")
            return str(narrative or "")
        return str(narrative_result or "")

    def _active_effects(self, values: Any) -> list[str]:
        effects: list[str] = []
        for item in values.values():
            if not isinstance(item, dict):
                continue
            if "active_effects" in item:
                effects.extend(str(effect).strip() for effect in item.get("active_effects", []) if str(effect).strip())
            else:
                for nested in item.values():
                    if not isinstance(nested, dict):
                        continue
                    effects.extend(str(effect).strip() for effect in nested.get("active_effects", []) if str(effect).strip())
        return effects

    def _enrich_character(self, character: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        character_id = character.get("id", "")
        return {
            "id": character_id,
            "name": self._character_display_name(state, character_id),
            "order": character.get("order", 1),
            "player_input": self._normalize_player_input(character.get("player_input", {})),
            "context": character.get("context", ""),
        }

    def _normalize_player_input(self, player_input: Any) -> dict[str, str]:
        if not isinstance(player_input, dict):
            return {"intent": "", "action": "", "speech": ""}
        return {
            "intent": str(player_input.get("intent", "") or "").strip(),
            "action": str(player_input.get("action", "") or "").strip(),
            "speech": str(player_input.get("speech", "") or "").strip(),
        }

    def _log_complete_output(self, source: str, raw_text: str) -> None:
        print(f"\n===== LLM OUTPUT [{source}] =====\n{raw_text}\n===== END [{source}] =====\n")
