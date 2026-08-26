from __future__ import annotations

from typing import Any, Iterator

from .stages import character_dialogue, character_reflection
from .stream_parsers.xml_protocol import CharacterStreamParser, parse_character_xml


class CharacterAgent:
    """Runs character dialogue and reflection stages."""

    def __init__(self, llm_client=None, thinking: bool = False, state_manager=None) -> None:
        self.llm_client = llm_client
        self.thinking_enabled = thinking
        self.state_manager = state_manager

    def act(self, character_id: str, plan_context: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        state = self._resolve_state(state)
        character_context = self._load_character_context(character_id, state)
        if self.llm_client:
            try:
                raw_text = self.llm_client.generate_text(
                    system_prompt=character_dialogue.build_system_prompt(character_id, character_context),
                    user_prompt=character_dialogue.build_user_prompt(character_id, character_context, plan_context),
                )
                self._log_complete_output(character_id, raw_text)
                result = parse_character_xml(raw_text, character_id)
                return self._dialogue_part(character_id, result, character_context)
            except Exception:
                pass
        return self._dialogue_part(
            character_id,
            self._build_fallback_response(character_id, character_context, plan_context),
            character_context,
        )

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
            payload = self._dialogue_part(character_id, result, character_context)
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
                "data": {
                    "stage": "character",
                    "character_id": character_id,
                    "character_feedback": result,
                    "payload": payload,
                },
            }
            return

        parser = CharacterStreamParser(character_id, thinking=self.thinking_enabled)
        raw_parts: list[str] = []
        for delta in self.llm_client.stream_text(
            system_prompt=character_dialogue.build_system_prompt(character_id, character_context),
            user_prompt=character_dialogue.build_user_prompt(character_id, character_context, plan_context),
        ):
            if not delta:
                continue
            raw_parts.append(delta)
            yield from self._stream_parser_events(parser.feed(delta), "character", character_id)

        raw_text = "".join(raw_parts)
        if raw_text:
            self._log_complete_output(character_id, raw_text)
        result = (
            parse_character_xml(raw_text, character_id)
            if raw_text
            else self._build_fallback_response(character_id, character_context, plan_context)
        )
        result["name"] = character_context.get("state", {}).get("name", character_id)
        payload = self._dialogue_part(character_id, result, character_context)
        yield {
            "event": "stage_done",
            "data": {
                "stage": "character",
                "character_id": character_id,
                "character_feedback": result,
                "payload": payload,
            },
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
                    system_prompt=character_reflection.build_system_prompt(character_id, character_context),
                    user_prompt=character_reflection.build_user_prompt(character_id, character_context, reflection_context),
                )
                self._log_complete_output(f"{character_id}:reflection", raw_text)
                result = parse_character_xml(raw_text, character_id)
                return self._reflection_part(character_id, result, character_context)
            except Exception:
                pass
        return self._reflection_part(
            character_id,
            self._build_fallback_reflection(character_id, character_context),
            character_context,
        )

    def remember_event(
        self,
        character_id: str,
        event_context: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        state = self._resolve_state(state)
        character_context = self._load_character_context(character_id, state)
        if self.llm_client:
            try:
                raw_text = self.llm_client.generate_text(
                    system_prompt=character_reflection.build_event_memory_system_prompt(character_id, character_context),
                    user_prompt=character_reflection.build_event_memory_user_prompt(character_id, character_context, event_context),
                )
                self._log_complete_output(f"{character_id}:event_memory", raw_text)
                result = parse_character_xml(raw_text, character_id)
                return self._event_memory_part(character_id, result, character_context)
            except Exception:
                pass
        return self._event_memory_part(character_id, {"memory_append": ""}, character_context)

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
            payload = self._reflection_part(character_id, result, character_context)
            for index, block_name in enumerate(("emotion", "location", "state_update", "memory_append")):
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
                "data": {
                    "stage": "character_reflection",
                    "character_id": character_id,
                    "character_reflection": result,
                    "payload": payload,
                },
            }
            return

        parser = CharacterStreamParser(character_id, thinking=self.thinking_enabled)
        raw_parts: list[str] = []
        for delta in self.llm_client.stream_text(
            system_prompt=character_reflection.build_system_prompt(character_id, character_context),
            user_prompt=character_reflection.build_user_prompt(character_id, character_context, reflection_context),
        ):
            if not delta:
                continue
            raw_parts.append(delta)
            yield from self._stream_parser_events(parser.feed(delta), "character_reflection", character_id)

        raw_text = "".join(raw_parts)
        if raw_text:
            self._log_complete_output(f"{character_id}:reflection", raw_text)
        result = (
            parse_character_xml(raw_text, character_id)
            if raw_text
            else self._build_fallback_reflection(character_id, character_context)
        )
        result["name"] = character_context.get("state", {}).get("name", character_id)
        payload = self._reflection_part(character_id, result, character_context)
        yield {
            "event": "stage_done",
            "data": {
                "stage": "character_reflection",
                "character_id": character_id,
                "character_reflection": result,
                "payload": payload,
            },
        }

    def _stream_parser_events(
        self,
        parsed_events: Iterator[dict[str, Any]],
        stage: str,
        character_id: str,
    ) -> Iterator[dict[str, Any]]:
        for parsed_event in parsed_events:
            event_type = parsed_event["type"]
            if event_type in {"thinking_started", "thinking_delta", "thinking_done"}:
                yield {
                    "event": event_type,
                    "data": {
                        "source": stage,
                        "character_id": character_id,
                        "delta": parsed_event.get("delta", ""),
                        "text": parsed_event.get("text", ""),
                    },
                }
            elif event_type in {"block_started", "block_delta", "block_done"}:
                yield {
                    "event": event_type,
                    "data": {
                        "stage": stage,
                        "character_id": character_id,
                        "block": parsed_event.get("block", ""),
                        "block_index": parsed_event.get("block_index", 0),
                        "attrs": parsed_event.get("attrs", {}),
                        "delta": parsed_event.get("delta", ""),
                        "text": parsed_event.get("text", ""),
                        "parsed": parsed_event.get("parsed"),
                    },
                }

    def _load_character_context(self, character_id: str, state: dict[str, Any]) -> dict[str, Any]:
        return state.get("characters", {}).get(character_id, {"profile": "", "state": {}, "memory": ""})

    def _dialogue_part(self, character_id: str, result: dict[str, Any], character_context: dict[str, Any]) -> dict[str, Any]:
        name = character_context.get("state", {}).get("name", character_id)
        dialogue = {"response": result.get("response", "")}
        if result.get("audience"):
            dialogue["audience"] = result["audience"]
        return {"characters": {character_id: {"id": character_id, "name": name, "dialogue": dialogue}}}

    def _reflection_part(self, character_id: str, result: dict[str, Any], character_context: dict[str, Any]) -> dict[str, Any]:
        name = character_context.get("state", {}).get("name", character_id)
        reflection = {
            "emotion": result.get("emotion", ""),
            "location": result.get("location", {}),
            "state_update": result.get("state_update", {}),
            "memory_append": result.get("memory_append", ""),
        }
        return {"characters": {character_id: {"id": character_id, "name": name, "reflection": reflection}}}

    def _event_memory_part(self, character_id: str, result: dict[str, Any], character_context: dict[str, Any]) -> dict[str, Any]:
        name = character_context.get("state", {}).get("name", character_id)
        event_memory = {"memory_append": result.get("memory_append", "")}
        return {"characters": {character_id: {"id": character_id, "name": name, "event_memory": event_memory}}}

    def _resolve_state(self, fallback_state: dict[str, Any]) -> dict[str, Any]:
        if self.state_manager is not None:
            return self.state_manager.get_agent_state_view()
        return fallback_state

    def _build_fallback_response(
        self,
        character_id: str,
        character_context: dict[str, Any],
        plan_context: dict[str, Any],
    ) -> dict[str, Any]:
        content = plan_context.get("context") or "……"
        name = character_context.get("state", {}).get("name", character_id)
        return {
            "character_id": character_id,
            "name": name,
            "response": f"{name}听完后短暂停顿：“关于‘{content}’，我需要再想想。”",
        }

    def _build_fallback_reflection(self, character_id: str, character_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "character_id": character_id,
            "name": character_context.get("state", {}).get("name", character_id),
            "response": "",
            "emotion": character_context.get("state", {}).get("emotion", ""),
            "location": {},
            "state_update": {},
            "memory_append": "",
        }

    def _log_complete_output(self, character_id: str, raw_text: str) -> None:
        print(f"\n===== LLM OUTPUT [character:{character_id}] =====\n{raw_text}\n===== END [character:{character_id}] =====\n")
