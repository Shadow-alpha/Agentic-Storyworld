from __future__ import annotations

import json
from typing import Any, Iterator

from .prompts import Character_Dialogue_Prompt, Character_Reflection_Prompt
from .stream_parsers.xml_protocol import CharacterStreamParser, parse_character_xml


class CharacterAgent:
    """Produces a single character response for one planned character task."""

    def __init__(self, llm_client=None, thinking: bool = False, state_manager=None) -> None:
        self.llm_client = llm_client
        self.thinking_enabled = thinking
        self.state_manager = state_manager

    def act(self, character_id: str, plan_context: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        state = self._resolve_state(state)
        character_context = self._load_character_context(character_id, state)
        if self.llm_client:
            try:
                system_prompt = self._build_dialogue_system_prompt(character_id, character_context)
                user_prompt = self._build_dialogue_prompt(character_id, character_context, plan_context)
                raw_text = self.llm_client.generate_text(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                self._log_complete_output(character_id, raw_text)
                return self._normalize_result(parse_character_xml(raw_text, character_id), character_context)
            except Exception:
                pass
        return self._build_fallback_response(character_id, character_context, plan_context)

    def stream_act(
        self,
        character_id: str,
        plan_context: dict[str, Any],
        state: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        state = self._resolve_state(state)
        character_context = self._load_character_context(character_id, state)
        yield {"event": "stage_started", "data": {"stage": "character", "character_id": character_id}}
        if not self.llm_client:
            result = self._build_fallback_response(character_id, character_context, plan_context)
            yield {
                "event": "block_done",
                "data": {
                    "stage": "character",
                    "character_id": character_id,
                    "block": "response",
                    "block_index": 0,
                    "parsed": result.get("response", ""),
                },
            }
            yield {
                "event": "stage_done",
                "data": {"stage": "character", "character_id": character_id, "character_feedback": result},
            }
            return

        parser = CharacterStreamParser(character_id, thinking=self.thinking_enabled)
        raw_parts: list[str] = []
        accumulated_result = {
            "character_id": character_id,
            "name": character_context.get("state", {}).get("name", character_id),
            "response": "",
        }
        system_prompt = self._build_dialogue_system_prompt(character_id, character_context)
        user_prompt = self._build_dialogue_prompt(character_id, character_context, plan_context)
        for delta in self.llm_client.stream_text(
            system_prompt=system_prompt,
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
                            "source": "character",
                            "character_id": character_id,
                            "delta": parsed_event.get("delta", ""),
                            "text": parsed_event.get("text", ""),
                        },
                    }
                elif event_type in {"block_started", "block_delta", "block_done"}:
                    parsed = parsed_event.get("parsed")
                    if event_type == "block_done":
                        block_name = parsed_event.get("block", "")
                        if block_name == "response":
                            accumulated_result["response"] = parsed or ""
                        parsed = accumulated_result.get(block_name, parsed)
                    yield {
                        "event": event_type,
                        "data": {
                            "stage": "character",
                            "character_id": character_id,
                            "block": parsed_event.get("block", ""),
                            "block_index": parsed_event.get("block_index", 0),
                            "attrs": parsed_event.get("attrs", {}),
                            "delta": parsed_event.get("delta", ""),
                            "text": parsed_event.get("text", ""),
                            "parsed": parsed,
                        },
                    }

        raw_text = "".join(raw_parts)
        if raw_text:
            self._log_complete_output(character_id, raw_text)
            if not accumulated_result.get("response"):
                parsed_result = parse_character_xml(raw_text, character_id)
                accumulated_result["response"] = parsed_result.get("response", "")
        result = accumulated_result
        if not raw_text:
            result = self._build_fallback_response(character_id, character_context, plan_context)
        yield {
            "event": "stage_done",
            "data": {"stage": "character", "character_id": character_id, "character_feedback": result},
        }

    def reflect(
        self,
        character_id: str,
        reflection_context: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        state = self._resolve_state(state)
        character_context = self._load_character_context(character_id, state)
        if self.llm_client:
            try:
                raw_text = self.llm_client.generate_text(
                    system_prompt=self._build_reflection_system_prompt(character_id, character_context),
                    user_prompt=self._build_reflection_prompt(character_id, character_context, reflection_context),
                )
                self._log_complete_output(f"{character_id}:reflection", raw_text)
                return self._normalize_result(parse_character_xml(raw_text, character_id), character_context)
            except Exception:
                pass
        return self._build_fallback_reflection(character_id, character_context)

    def stream_reflect(
        self,
        character_id: str,
        reflection_context: dict[str, Any],
        state: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        state = self._resolve_state(state)
        character_context = self._load_character_context(character_id, state)
        yield {"event": "stage_started", "data": {"stage": "character_reflection", "character_id": character_id}}
        if not self.llm_client:
            result = self._build_fallback_reflection(character_id, character_context)
            for index, block_name in enumerate(("emotion", "state_update", "memory_append")):
                yield {
                    "event": "block_done",
                    "data": {
                        "stage": "character_reflection",
                        "character_id": character_id,
                        "block": block_name,
                        "block_index": index,
                        "parsed": result.get(block_name),
                    },
                }
            yield {
                "event": "stage_done",
                "data": {"stage": "character_reflection", "character_id": character_id, "character_reflection": result},
            }
            return

        parser = CharacterStreamParser(character_id, thinking=self.thinking_enabled)
        raw_parts: list[str] = []
        accumulated_result = {
            "character_id": character_id,
            "name": character_context.get("state", {}).get("name", character_id),
            "response": "",
            "emotion": "",
            "state_update": {},
            "memory_append": "",
        }
        for delta in self.llm_client.stream_text(
            system_prompt=self._build_reflection_system_prompt(character_id, character_context),
            user_prompt=self._build_reflection_prompt(character_id, character_context, reflection_context),
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
                            "source": "character_reflection",
                            "character_id": character_id,
                            "delta": parsed_event.get("delta", ""),
                            "text": parsed_event.get("text", ""),
                        },
                    }
                elif event_type in {"block_started", "block_delta", "block_done"}:
                    parsed = parsed_event.get("parsed")
                    if event_type == "block_done":
                        block_name = parsed_event.get("block", "")
                        if block_name == "emotion":
                            accumulated_result["emotion"] = parsed or ""
                        elif block_name == "state_update":
                            accumulated_result["state_update"] = parsed or {}
                        elif block_name == "memory_append":
                            memory = parsed or {}
                            accumulated_result["memory_append"] = memory.get("text", "") if isinstance(memory, dict) else ""
                        elif block_name:
                            accumulated_result[block_name] = parsed
                        parsed = accumulated_result.get(block_name, parsed)
                    yield {
                        "event": event_type,
                        "data": {
                            "stage": "character_reflection",
                            "character_id": character_id,
                            "block": parsed_event.get("block", ""),
                            "block_index": parsed_event.get("block_index", 0),
                            "attrs": parsed_event.get("attrs", {}),
                            "delta": parsed_event.get("delta", ""),
                            "text": parsed_event.get("text", ""),
                            "parsed": parsed,
                        },
                    }

        raw_text = "".join(raw_parts)
        if raw_text:
            self._log_complete_output(f"{character_id}:reflection", raw_text)
            if not accumulated_result.get("emotion") and not accumulated_result.get("state_update"):
                parsed_result = parse_character_xml(raw_text, character_id)
                accumulated_result["emotion"] = parsed_result.get("emotion", "")
                accumulated_result["state_update"] = parsed_result.get("state_update", {})
                accumulated_result["memory_append"] = parsed_result.get("memory_append", "")
        result = accumulated_result if raw_text else self._build_fallback_reflection(character_id, character_context)
        yield {
            "event": "stage_done",
            "data": {"stage": "character_reflection", "character_id": character_id, "character_reflection": result},
        }

    def _load_character_context(self, character_id: str, state: dict[str, Any]) -> dict[str, Any]:
        return state.get("characters", {}).get(character_id, {"profile": "", "state": {}, "memory": ""})

    def _resolve_state(self, fallback_state: dict[str, Any]) -> dict[str, Any]:
        if self.state_manager is not None:
            return self.state_manager.get_agent_state_view()
        return fallback_state

    def _build_dialogue_prompt(
        self,
        character_id: str,
        character_context: dict[str, Any],
        plan_context: dict[str, Any],
    ) -> str:
        state_snapshot = character_context.get("state", {})
        dialogue_state = self._format_dialogue_state(state_snapshot)
        profile_excerpt = character_context.get("profile", "")
        memory_context = self._format_memory_context(character_context.get("memory"))
        turn_dialogue = self._format_turn_dialogue(plan_context.get("turn_dialogue", []), character_id)
        return (
            "PROFILE:\n"
            f"{profile_excerpt}\n\n"
            "STATE:\n"
            f"{dialogue_state}\n\n"
            "MEMORY:\n"
            f"{memory_context}\n\n"
            "CONTEXT:\n"
            f"{plan_context.get('context', '')}\n\n"
            "USER_INTENT:\n"
            f"{plan_context.get('user_intent', '')}\n\n"
            "TURN_DIALOGUE:\n"
            f"{turn_dialogue}\n\n"
            "Return strict TAG format with <response> only."
        )

    def _build_reflection_prompt(
        self,
        character_id: str,
        character_context: dict[str, Any],
        reflection_context: dict[str, Any],
    ) -> str:
        state_snapshot = character_context.get("state", {})
        profile_excerpt = character_context.get("profile", "")
        memory_context = self._format_memory_context(character_context.get("memory"))
        raw_response = reflection_context.get("raw_response", reflection_context.get("response", ""))
        final_narrative = reflection_context.get(
            "final_narrative",
            reflection_context.get("narrative_result", reflection_context.get("narrative", "")),
        )
        final_narrative = self._reflection_narrative_text(final_narrative)
        updatable_state = {
            "stats": self._without_active_effects(state_snapshot.get("stats", {})),
            "relations": self._without_active_effects(state_snapshot.get("relations", {})),
        }
        return (
            "PROFILE:\n"
            f"{profile_excerpt}\n\n"
            "CURRENT EMOTION:\n"
            f"{state_snapshot.get('emotion') or 'unknown'}\n\n"
            "UPDATABLE STATE:\n"
            f"{self._format_prompt_value(updatable_state)}\n\n"
            "MEMORY:\n"
            f"{memory_context}\n\n"
            "YOUR RAW RESPONSE:\n"
            f"{self._format_prompt_value(raw_response)}\n\n"
            "FINAL NARRATIVE:\n"
            f"{self._format_prompt_value(final_narrative)}\n\n"
            "Return strict TAG format with <emotion>, <state_update>, and <memory_append>."
        )

    def _format_dialogue_state(self, state_snapshot: Any) -> str:
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

        stat_lines: list[str] = []
        stats = state_snapshot.get("stats", {})
        if isinstance(stats, dict):
            for item in stats.values():
                if not isinstance(item, dict):
                    continue
                effects = item.get("active_effects", [])
                if isinstance(effects, list):
                    stat_lines.extend(f"  - {str(effect).strip()}" for effect in effects if str(effect).strip())
                elif str(effects).strip():
                    stat_lines.append(f"  - {str(effects).strip()}")

        relation_lines: list[str] = []
        relations = state_snapshot.get("relations", {})
        player_relation = relations.get("player", {}) if isinstance(relations, dict) else {}
        if isinstance(player_relation, dict):
            effects = player_relation.get("active_effects", [])
            if isinstance(effects, list):
                relation_lines.extend(f"  - {str(effect).strip()}" for effect in effects if str(effect).strip())
            elif str(effects).strip():
                relation_lines.append(f"  - {str(effects).strip()}")

        possession_lines: list[str] = []
        possessions = state_snapshot.get("possessions", [])
        if isinstance(possessions, list):
            for item in possessions:
                if isinstance(item, dict):
                    item_id = str(item.get("id", "")).strip()
                    name = str(item.get("name", "") or item_id).strip()
                    description = str(item.get("description", "")).strip()
                    label = f"{name} ({item_id})" if item_id and item_id != name else name
                    if label:
                        possession_lines.append(f"  - {label}：{description}" if description else f"  - {label}")
                elif item:
                    possession_lines.append(f"  - {item}")

        return "\n".join(
            [
                f"- 当前情绪：{state_snapshot.get('emotion') or '未知'}",
                f"- 所在场景：{location_text}",
                "- 当前状态影响：",
                *(stat_lines or ["  - 暂无明显状态影响"]),
                "- 对玩家态度：",
                *(relation_lines or ["  - 暂无明确态度影响"]),
                "- 持有物品：",
                *(possession_lines or ["  - 无"]),
            ]
        )

    def _format_turn_dialogue(self, turn_dialogue: Any, current_character_id: str) -> str:
        if not isinstance(turn_dialogue, list):
            return "(none)"
        lines: list[str] = []
        for item in turn_dialogue:
            if not isinstance(item, dict):
                continue
            speaker_id = str(item.get("character_id", "")).strip()
            text_key = "raw_response" if speaker_id == current_character_id else "visible_dialogue"
            text = str(item.get(text_key) or "").strip()
            if not text:
                continue
            label = speaker_id or "unknown"
            lines.append(f"[{label}]\n{text}")
        return "\n\n".join(lines) if lines else "(none)"

    def _format_memory_context(self, memory: Any) -> str:
        if isinstance(memory, dict):
            core = self._format_memory_items(memory.get("core", []))
            turns = self._format_memory_items(memory.get("turns", []))
            return (
                "CORE MEMORY:\n"
                f"{core or '(none)'}\n\n"
                "RECENT TURN MEMORY:\n"
                f"{turns or '(none)'}"
            )
        memory_text = str(memory or "").strip()
        return memory_text[-600:] if memory_text else "(none)"

    def _format_memory_items(self, items: Any) -> str:
        if not isinstance(items, list):
            return ""
        lines: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            turn = item.get("turn")
            text = str(item.get("text", "")).strip()
            if text:
                label = f"[Turn {turn}]" if turn else "[Turn ?]"
                lines.append(f"{label} {text}")
        return "\n".join(lines)

    def _format_prompt_value(self, value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, indent=2)
        return str(value or "")

    def _without_active_effects(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._without_active_effects(item) for key, item in value.items() if key != "active_effects"}
        if isinstance(value, list):
            return [self._without_active_effects(item) for item in value]
        return value

    def _reflection_narrative_text(self, final_narrative: Any) -> str:
        if isinstance(final_narrative, dict):
            narrative = final_narrative.get("narrative", final_narrative)
            if isinstance(narrative, dict):
                return str(narrative.get("visible", "") or "")
            return str(narrative or "")
        return str(final_narrative or "")

    def _build_dialogue_system_prompt(self, character_id: str, character_context: dict[str, Any]) -> str:
        state_snapshot = character_context.get("state", {})
        return Character_Dialogue_Prompt.format(
            character_name=state_snapshot.get("name", character_id),
            character_id=character_id,
        )

    def _build_reflection_system_prompt(self, character_id: str, character_context: dict[str, Any]) -> str:
        state_snapshot = character_context.get("state", {})
        return Character_Reflection_Prompt.format(
            character_name=state_snapshot.get("name", character_id),
            character_id=character_id,
        )

    def _normalize_result(self, result: dict[str, Any], character_context: dict[str, Any]) -> dict[str, Any]:
        state = character_context.get("state", {})
        return {
            "character_id": result.get("character_id", ""),
            "name": state.get("name", result.get("character_id", "")),
            "response": result.get("response", ""),
            "emotion": result.get("emotion", ""),
            "state_update": result.get("state_update", {}),
            "memory_append": result.get("memory_append", ""),
        }

    def _build_fallback_response(
        self,
        character_id: str,
        character_context: dict[str, Any],
        plan_context: dict[str, Any],
    ) -> dict[str, Any]:
        content = plan_context.get("context") or "……"
        name = character_context.get("state", {}).get("name", character_id)
        response = f"{name}听完后短暂停顿：‘关于“{content}”，我需要再想想。’"
        return {
            "character_id": character_id,
            "name": name,
            "response": response,
        }

    def _build_fallback_reflection(self, character_id: str, character_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "character_id": character_id,
            "name": character_context.get("state", {}).get("name", character_id),
            "response": "",
            "emotion": character_context.get("state", {}).get("emotion", ""),
            "state_update": {},
            "memory_append": "",
        }

    def _log_complete_output(self, character_id: str, raw_text: str) -> None:
        print(f"\n===== LLM OUTPUT [character:{character_id}] =====\n{raw_text}\n===== END [character:{character_id}] =====\n")
