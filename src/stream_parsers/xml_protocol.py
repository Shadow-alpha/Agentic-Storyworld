from __future__ import annotations

import ast
import json
import re
import xml.etree.ElementTree as ET
from typing import Any


def parse_xml_fragment(text: str, source: str = "xml") -> ET.Element:
    cleaned = text.strip().strip("```xml").strip("```").strip()
    cleaned = re.sub(r"<\?xml[^>]*\?>", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = _strip_leading_thinking_block(cleaned)
    cleaned = _repair_pseudo_kv_xml(cleaned)
    if not cleaned:
        raise ValueError("Empty XML payload.")
    try:
        return ET.fromstring(cleaned)
    except ET.ParseError as first_error:
        try:
            return ET.fromstring(f"<root>{cleaned}</root>")
        except ET.ParseError as second_error:
            print(
                f"\n===== XML PARSE ERROR [{source}] =====\n"
                f"{second_error}\n"
                "----- RAW XML -----\n"
                f"{cleaned}\n"
                f"===== END XML PARSE ERROR [{source}] =====\n"
            )
            return ET.Element("root")


def node_text(node: Any) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def parse_kv_block(text: str, parse_reasons: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {}
    section_stack: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        normalized_line = _repair_pseudo_kv_xml(raw_line)
        indent = len(normalized_line) - len(normalized_line.lstrip(" "))
        line = normalized_line.strip()
        if not line:
            continue
        separator = ":" if ":" in line else "=" if "=" in line else None
        if separator is None:
            continue
        key, raw_value = line.split(separator, 1)
        key = key.strip()
        if not key:
            continue
        while section_stack and indent <= section_stack[-1][0]:
            section_stack.pop()
        full_key = ".".join([prefix for _, prefix in section_stack] + [key])
        raw_value = raw_value.strip()
        if raw_value == "":
            section_stack.append((indent, full_key))
            continue
        value = _coerce_value_with_reason(raw_value) if parse_reasons else _coerce_scalar(raw_value)
        if parse_reasons and full_key.endswith(".value"):
            parent_key = full_key[: -len(".value")]
            if isinstance(value, dict):
                _assign_nested(data, parent_key, value)
            else:
                _assign_nested(data, parent_key, {"value": value})
            continue
        _assign_nested(data, full_key, value)
    return data


def parse_protocol_blocks(text: str, parsers: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract complete top-level XML-ish blocks and parse known block types."""
    cleaned = _prepare_protocol_text(text)
    parser_map = {key.lower(): value for key, value in (parsers or {}).items()}
    records = []
    by_name: dict[str, list[dict[str, Any]]] = {}
    for raw_block in _extract_top_level_complete_blocks(cleaned):
        record = _parse_protocol_block(raw_block, parser_map)
        records.append(record)
        by_name.setdefault(record["name"], []).append(record)
    return {"items": records, "by_name": by_name}


def first_block_parsed(blocks: dict[str, Any], name: str, default: Any = None) -> Any:
    items = blocks.get("by_name", {}).get(name.lower(), [])
    if not items:
        return default
    return items[0].get("parsed", default)


def parse_plan_xml(text: str) -> dict[str, Any]:
    characters = []
    cleaned = _prepare_protocol_text(text)
    for block in _extract_plan_character_blocks(cleaned):
        character = parse_plan_character_block(block)
        character_id = character.get("id", "")
        if not character_id:
            continue
        characters.append(character)
    return {
        "user_intent": _extract_tag_inner_text(cleaned, "user_intent"),
        "context": _extract_tag_inner_text(cleaned, "context"),
        "characters": characters,
        "director_meta": {},
    }


def _extract_plan_character_blocks(text: str) -> list[str]:
    pattern = re.compile(
        r"<character\b[^>]*/\s*>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return [match.group(0) for match in pattern.finditer(text)]


def parse_plan_character_block(xml_text: str) -> dict[str, Any]:
    order_text = _extract_attribute(xml_text, "character", "order").strip()
    try:
        order = int(order_text) if order_text else 1
    except ValueError:
        order = 1
    return {
        "id": _extract_attribute(xml_text, "character", "id").strip(),
        "order": order,
    }


def parse_character_xml(text: str, character_id: str) -> dict[str, Any]:
    cleaned = _prepare_protocol_text(text)
    blocks = parse_protocol_blocks(
        cleaned,
        {
            "response": parse_text_block,
            "emotion": parse_text_block,
            "state_update": parse_character_state_update_block,
            "memory_append": parse_memory_append_block,
        },
    )
    memory = first_block_parsed(blocks, "memory_append", {}) or {}
    return {
        "character_id": character_id,
        "response": first_block_parsed(blocks, "response", "") or _extract_unclosed_tag_text(cleaned, "response"),
        "emotion": first_block_parsed(blocks, "emotion", "") or "",
        "state_update": first_block_parsed(blocks, "state_update", {}) or {},
        "memory_append": memory.get("text", "") if isinstance(memory, dict) else "",
    }


def parse_integrate_xml(text: str) -> dict[str, Any]:
    cleaned = _prepare_protocol_text(text)
    blocks = parse_protocol_blocks(
        cleaned,
        {
            "narrative": parse_narrative_block,
            "summary": parse_text_block,
            "goal": parse_goal_block,
            "goal_update": parse_goal_update_block,
            "state_update": parse_state_update_block,
            "interaction": parse_interaction_block,
        },
    )
    narrative = first_block_parsed(blocks, "narrative", {"visible": "", "hidden": ""})
    summary = first_block_parsed(blocks, "summary", "") or ""
    goal = first_block_parsed(blocks, "goal", {}) or {}
    goal_update = first_block_parsed(blocks, "goal_update", {"checkpoints": []}) or {"checkpoints": []}
    state_update = first_block_parsed(blocks, "state_update", {"world_state": {}, "user_state": {}})
    interaction = first_block_parsed(blocks, "interaction", {"mode": "hybrid", "options": []})
    return {
        "narrative": narrative,
        "summary": summary,
        "goal": {
            "current_goal": goal.get("current_goal", ""),
            "status": goal.get("status", "in_progress"),
            "progress": goal.get("progress", 0),
            "notes": goal.get("notes", ""),
        },
        "goal_update": goal_update,
        "interaction": interaction,
        "state_update": state_update,
    }


def parse_narrative_xml(text: str) -> dict[str, Any]:
    cleaned = _prepare_protocol_text(text)
    blocks = parse_protocol_blocks(
        cleaned,
        {
            "time": parse_text_block,
            "scene": parse_scene_block,
            "narrative": parse_narrative_block,
            "summary": parse_text_block,
            "movement": parse_movement_block,
        },
    )
    return {
        "time": first_block_parsed(blocks, "time", "") or "",
        "scene": first_block_parsed(blocks, "scene", "") or "",
        "narrative": first_block_parsed(blocks, "narrative", {"visible": "", "hidden": ""})
        or {"visible": "", "hidden": ""},
        "summary": first_block_parsed(blocks, "summary", "") or "",
        "movement": first_block_parsed(blocks, "movement", []) or [],
    }


def parse_resolve_xml(text: str) -> dict[str, Any]:
    cleaned = _prepare_protocol_text(text)
    blocks = parse_protocol_blocks(
        cleaned,
        {
            "state_update": parse_state_update_block,
            "goal_update": parse_goal_update_block,
            "interaction": parse_interaction_block,
        },
    )
    return {
        "state_update": first_block_parsed(blocks, "state_update", {"world_state": {}, "user_state": {}}),
        "goal_update": first_block_parsed(blocks, "goal_update", {"checkpoints": []}) or {"checkpoints": []},
        "interaction": first_block_parsed(blocks, "interaction", {"mode": "hybrid", "options": []}),
    }


def parse_ending_xml(text: str) -> dict[str, str]:
    cleaned = _prepare_protocol_text(text)
    blocks = parse_protocol_blocks(cleaned, {"ending_narrative": parse_text_block})
    narrative = first_block_parsed(blocks, "ending_narrative", "") or _extract_unclosed_tag_text(
        cleaned,
        "ending_narrative",
    )
    return {"narrative": narrative.strip()}


def parse_scene_block(xml_text: str) -> dict[str, str]:
    return {
        "id": _extract_attribute(xml_text, "scene", "id").strip(),
        "name": _extract_tag_inner_text(xml_text, "scene"),
    }


def parse_narrative_block(xml_text: str) -> dict[str, Any]:
    inner_text = _extract_tag_inner_text(xml_text, "narrative") if xml_text.strip() else ""
    return {
        "visible": inner_text,
        "hidden": "",
    }


def parse_movement_block(xml_text: str) -> list[dict[str, str]]:
    movements = []
    for block in _extract_all_complete_blocks(xml_text, "character"):
        character_id = _extract_attribute(block, "character", "id").strip()
        location = _extract_attribute(block, "character", "location").strip()
        if not character_id or not location:
            continue
        movements.append(
            {
                "character_id": character_id,
                "location": location,
                "reason": _extract_tag_inner_text(block, "character"),
            }
        )
    return movements


def parse_goal_block(xml_text: str) -> dict[str, Any]:
    if not xml_text.strip():
        return {}
    return parse_kv_block(_extract_tag_inner_text(xml_text, "goal"))


def parse_text_block(xml_text: str) -> str:
    tag_name = _extract_start_tag_name(xml_text)
    return _extract_tag_inner_text(xml_text, tag_name) if tag_name else node_text(parse_xml_fragment(xml_text))


def parse_goal_update_block(xml_text: str) -> dict[str, Any]:
    if not xml_text.strip():
        return {"checkpoints": []}
    checkpoints = []
    for block in _extract_all_complete_blocks(xml_text, "checkpoint"):
        goal_id = _extract_attribute(block, "checkpoint", "goal_id").strip()
        checkpoint_id = _extract_attribute(block, "checkpoint", "checkpoint_id").strip()
        status = (_extract_attribute(block, "checkpoint", "status") or "in_progress").strip().lower()
        if not goal_id or not checkpoint_id:
            continue
        progress_note = _extract_tag_inner_text(block, "checkpoint")
        item = {
            "goal_id": goal_id,
            "checkpoint_id": checkpoint_id,
            "status": status,
            "progress_note": progress_note,
        }
        checkpoints.append(item)
    return {"checkpoints": checkpoints}


def parse_interaction_block(xml_text: str) -> dict[str, Any]:
    options = []
    for index, option_block in enumerate(_extract_all_complete_blocks(xml_text, "option"), start=1):
        option = parse_option_block(option_block, index)
        if option.get("text"):
            options.append(option)
    return {"mode": "hybrid", "options": options}


def parse_state_update_block(xml_text: str) -> dict[str, Any]:
    if not xml_text.strip():
        return {"world_state": {}, "user_state": {}}
    inner_text = _extract_tag_inner_text(xml_text, "state_update")
    if not inner_text:
        return {"world_state": {}, "user_state": {}}
    world_block = _extract_complete_block(inner_text, "world_state")
    user_block = _extract_complete_block(inner_text, "user_state")
    if world_block is not None or user_block is not None:
        return {
            "world_state": parse_kv_block(
                "\n".join(
                    _strip_surrounding_angle_brackets(line)
                    for line in _repair_pseudo_kv_xml(_extract_tag_inner_text(world_block or "", "world_state")).splitlines()
                ),
                parse_reasons=True,
            ),
            "user_state": parse_kv_block(
                "\n".join(
                    _strip_surrounding_angle_brackets(line)
                    for line in _repair_pseudo_kv_xml(_extract_tag_inner_text(user_block or "", "user_state")).splitlines()
                ),
                parse_reasons=True,
            ),
        }
    lines = _repair_pseudo_kv_xml(inner_text)
    world_lines: list[str] = []
    user_lines: list[str] = []
    current_section: str | None = None
    for raw_line in lines.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        section_match = re.match(r"^</?(world_state|user_state)\s*>?$", stripped)
        if section_match:
            current_section = section_match.group(1) if not stripped.startswith("</") else None
            continue
        if stripped.startswith("<") and stripped.endswith(">") and not ":" in stripped:
            continue
        repaired = _strip_surrounding_angle_brackets(line)
        if current_section == "world_state":
            world_lines.append(repaired)
        elif current_section == "user_state":
            user_lines.append(repaired)
        else:
            stripped_repaired = repaired.lstrip()
            if stripped_repaired.startswith("player."):
                user_lines.append(f"stats.{stripped_repaired[len('player.'):]}")
            elif stripped_repaired.startswith("world."):
                world_lines.append(f"stats.{stripped_repaired[len('world.'):]}")
            elif stripped_repaired.startswith("user_state."):
                user_lines.append(stripped_repaired[len("user_state.") :])
            elif stripped_repaired.startswith("world_state."):
                world_lines.append(stripped_repaired[len("world_state.") :])
            else:
                world_lines.append(repaired)
    return {
        "world_state": parse_kv_block("\n".join(world_lines), parse_reasons=True),
        "user_state": parse_kv_block("\n".join(user_lines), parse_reasons=True),
    }


def parse_option_block(xml_text: str, default_index: int) -> dict[str, str]:
    option_id = _extract_attribute(xml_text, "option", "id") or f"option_{default_index}"
    option_text = _extract_tag_inner_text(xml_text, "option")
    if not option_text:
        return {"id": f"option_{default_index}", "text": ""}
    return {
        "id": option_id.strip(),
        "text": option_text,
    }


def parse_character_state_update_block(xml_text: str) -> dict[str, Any]:
    if not xml_text.strip():
        return {}
    inner_text = _extract_tag_inner_text(xml_text, "state_update")
    if not inner_text:
        return {}
    return parse_kv_block(
        "\n".join(_strip_surrounding_angle_brackets(line) for line in _repair_pseudo_kv_xml(inner_text).splitlines()),
        parse_reasons=True,
    )


def parse_memory_append_block(xml_text: str) -> dict[str, str]:
    return {
        "text": _extract_tag_inner_text(xml_text, "memory_append"),
    }


def _parse_protocol_block(raw_block: str, parsers: dict[str, Any]) -> dict[str, Any]:
    name = _extract_start_tag_name(raw_block).lower()
    inner = _extract_tag_inner_text(raw_block, name) if name else ""
    parser = parsers.get(name)
    if parser is not None:
        try:
            parsed = parser(raw_block)
        except Exception as error:
            print(f"\n===== BLOCK PARSE ERROR [{name}] =====\n{error}\n===== END BLOCK PARSE ERROR [{name}] =====\n")
            parsed = inner.strip()
    else:
        parsed = _strip_protocol_tags(inner).strip()
    return {
        "name": name,
        "attrs": _extract_attributes_from_start_tag(_extract_start_tag(raw_block)),
        "raw": raw_block,
        "inner": inner,
        "parsed": parsed,
    }


class ProtocolBlockStreamParser:
    """Incrementally emit generic events for complete top-level XML-ish blocks."""

    def __init__(self, parsers: dict[str, Any] | None = None) -> None:
        self.parsers = {key.lower(): value for key, value in (parsers or {}).items()}
        self.scan_pos = 0
        self.current: dict[str, Any] | None = None
        self.block_index = 0

    def feed(self, text: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while True:
            if self.current is None:
                match = re.search(r"<([A-Za-z_][\w.-]*)\b[^>]*>", text[self.scan_pos :], flags=re.IGNORECASE | re.DOTALL)
                if not match:
                    break
                start = self.scan_pos + match.start()
                open_end = self.scan_pos + match.end()
                name = match.group(1).lower()
                self.current = {
                    "name": name,
                    "start": start,
                    "open_end": open_end,
                    "start_tag": match.group(0),
                    "emitted": 0,
                    "index": self.block_index,
                }
                events.append(
                    {
                        "type": "block_started",
                        "block": name,
                        "block_index": self.block_index,
                        "attrs": _extract_attributes_from_start_tag(match.group(0)),
                    }
                )

            close_span = _find_close_tag_span(text, self.current["name"], self.current["open_end"])
            content_end = close_span[0] if close_span else len(text)
            available = max(0, content_end - self.current["open_end"])
            if available > self.current["emitted"]:
                full_text = text[self.current["open_end"] : content_end]
                delta = full_text[self.current["emitted"] :]
                self.current["emitted"] = available
                events.append(
                    {
                        "type": "block_delta",
                        "block": self.current["name"],
                        "block_index": self.current["index"],
                        "delta": delta,
                        "text": full_text,
                        "attrs": _extract_attributes_from_start_tag(self.current["start_tag"]),
                    }
                )
            if not close_span:
                break

            raw_block = text[self.current["start"] : close_span[1]]
            record = _parse_protocol_block(raw_block, self.parsers)
            events.append(
                {
                    "type": "block_done",
                    "block": self.current["name"],
                    "block_index": self.current["index"],
                    "text": record.get("inner", ""),
                    "parsed": record.get("parsed"),
                    "attrs": record.get("attrs", {}),
                }
            )
            self.scan_pos = close_span[1]
            self.current = None
            self.block_index += 1
        return events


class _TextTagTracker:
    def __init__(self, names: list[str], started_type: str, delta_type: str, done_type: str) -> None:
        self.names = names
        self.started_type = started_type
        self.delta_type = delta_type
        self.done_type = done_type
        self.open_start: int | None = None
        self.open_end: int | None = None
        self.close_name: str | None = None
        self.close_end: int | None = None
        self.emitted = 0
        self.started = False
        self.done = False

    def feed(self, text: str) -> list[dict[str, Any]]:
        if self.done:
            return []
        events: list[dict[str, Any]] = []
        if self.open_end is None:
            earliest_match = None
            earliest_name = None
            for name in self.names:
                match = re.search(fr"<{name}\b[^>]*>", text)
                if match and (earliest_match is None or match.start() < earliest_match.start()):
                    earliest_match = match
                    earliest_name = name
            if earliest_match is None:
                return events
            self.open_start = earliest_match.start()
            self.open_end = earliest_match.end()
            self.close_name = earliest_name
            if not self.started:
                self.started = True
                events.append({"type": self.started_type})
        close_tag = f"</{self.close_name}>"
        close_index = text.find(close_tag, self.open_end)
        content_end = close_index if close_index != -1 else len(text)
        available = max(0, content_end - self.open_end)
        if available > self.emitted:
            full_text = text[self.open_end:content_end]
            delta = full_text[self.emitted :]
            self.emitted = available
            events.append({"type": self.delta_type, "delta": delta, "text": full_text})
        if close_index != -1 and not self.done:
            self.done = True
            self.close_end = close_index + len(close_tag)
            events.append({"type": self.done_type, "text": text[self.open_end:close_index]})
        return events


class PlanStreamParser:
    def __init__(self, thinking: bool = False) -> None:
        self.raw_text = ""
        self.thinking_enabled = thinking
        self.thinking = _TextTagTracker(["think", "thinking"], "thinking_started", "thinking_delta", "thinking_done")
        self.scan_pos = 0
        self.character_index = 0

    def feed(self, delta: str) -> list[dict[str, Any]]:
        self.raw_text += delta
        events = self.thinking.feed(self.raw_text) if self.thinking_enabled else []
        body_text = self._body_text()
        if body_text is None:
            return events
        while True:
            match = re.search(r"<character\b[^>]*/\s*>", body_text[self.scan_pos :], flags=re.IGNORECASE | re.DOTALL)
            if not match:
                break
            open_end = self.scan_pos + match.end()
            start_tag = match.group(0)
            parsed = parse_plan_character_block(start_tag)
            character_id = parsed.get("id", "")
            if not character_id:
                self.scan_pos = open_end
                continue
            attrs = {"id": character_id, "order": parsed.get("order", 1)}
            events.append(
                {
                    "type": "block_started",
                    "block": "character",
                    "block_index": self.character_index,
                    "attrs": attrs,
                }
            )
            events.append(
                {
                    "type": "block_done",
                    "block": "character",
                    "block_index": self.character_index,
                    "attrs": attrs,
                    "text": start_tag,
                    "parsed": parsed,
                }
            )
            self.scan_pos = open_end
            self.character_index += 1
        return events

    def _body_text(self) -> str | None:
        if not self.thinking_enabled:
            return self.raw_text
        if self.thinking.started and not self.thinking.done:
            return None
        if self.thinking.done and self.thinking.close_end is not None:
            return self.raw_text[self.thinking.close_end :]
        return self.raw_text


class CharacterStreamParser:
    def __init__(self, character_id: str, thinking: bool = False) -> None:
        self.character_id = character_id
        self.raw_text = ""
        self.thinking_enabled = thinking
        self.thinking = _TextTagTracker(["think", "thinking"], "thinking_started", "thinking_delta", "thinking_done")
        self.blocks = ProtocolBlockStreamParser(
            {
                "response": parse_text_block,
                "emotion": parse_text_block,
                "state_update": parse_character_state_update_block,
                "memory_append": parse_memory_append_block,
            }
        )

    def feed(self, delta: str) -> list[dict[str, Any]]:
        self.raw_text += delta
        events = self.thinking.feed(self.raw_text) if self.thinking_enabled else []
        body_text = self._body_text()
        if body_text is None:
            return events
        events += self.blocks.feed(body_text)
        return events

    def _body_text(self) -> str | None:
        if not self.thinking_enabled:
            return self.raw_text
        if self.thinking.started and not self.thinking.done:
            return None
        if self.thinking.done and self.thinking.close_end is not None:
            return self.raw_text[self.thinking.close_end :]
        return self.raw_text


class IntegrateStreamParser:
    def __init__(self, thinking: bool = False) -> None:
        self.raw_text = ""
        self.thinking_enabled = thinking
        self.thinking = _TextTagTracker(["think", "thinking"], "thinking_started", "thinking_delta", "thinking_done")
        self.blocks = ProtocolBlockStreamParser(
            {
                "narrative": parse_narrative_block,
                "summary": parse_text_block,
                "goal": parse_goal_block,
                "goal_update": parse_goal_update_block,
                "state_update": parse_state_update_block,
                "interaction": parse_interaction_block,
            }
        )

    def feed(self, delta: str) -> list[dict[str, Any]]:
        self.raw_text += delta
        events = self.thinking.feed(self.raw_text) if self.thinking_enabled else []
        body_text = self._body_text()
        if body_text is None:
            return events
        events += self.blocks.feed(body_text)
        for event in events:
            if event.get("block") == "narrative" and event.get("type") == "block_delta":
                event["raw_text"] = event.get("text", "")
                event["display_text"] = event.get("text", "")
        return events

    def _body_text(self) -> str | None:
        if not self.thinking_enabled:
            return self.raw_text
        if self.thinking.started and not self.thinking.done:
            return None
        if self.thinking.done and self.thinking.close_end is not None:
            return self.raw_text[self.thinking.close_end :]
        return self.raw_text


class NarrativeStreamParser:
    def __init__(self, thinking: bool = False) -> None:
        self.raw_text = ""
        self.thinking_enabled = thinking
        self.thinking = _TextTagTracker(["think", "thinking"], "thinking_started", "thinking_delta", "thinking_done")
        self.blocks = ProtocolBlockStreamParser(
            {
                "time": parse_text_block,
                "scene": parse_scene_block,
                "narrative": parse_narrative_block,
                "summary": parse_text_block,
                "movement": parse_movement_block,
            }
        )

    def feed(self, delta: str) -> list[dict[str, Any]]:
        self.raw_text += delta
        events = self.thinking.feed(self.raw_text) if self.thinking_enabled else []
        body_text = self._body_text()
        if body_text is None:
            return events
        events += self.blocks.feed(body_text)
        for event in events:
            if event.get("block") == "narrative" and event.get("type") == "block_delta":
                event["raw_text"] = event.get("text", "")
                event["display_text"] = event.get("text", "")
        return events

    def _body_text(self) -> str | None:
        if not self.thinking_enabled:
            return self.raw_text
        if self.thinking.started and not self.thinking.done:
            return None
        if self.thinking.done and self.thinking.close_end is not None:
            return self.raw_text[self.thinking.close_end :]
        return self.raw_text


class ResolveStreamParser:
    def __init__(self, thinking: bool = False) -> None:
        self.raw_text = ""
        self.thinking_enabled = thinking
        self.thinking = _TextTagTracker(["think", "thinking"], "thinking_started", "thinking_delta", "thinking_done")
        self.blocks = ProtocolBlockStreamParser(
            {
                "state_update": parse_state_update_block,
                "goal_update": parse_goal_update_block,
                "interaction": parse_interaction_block,
            }
        )

    def feed(self, delta: str) -> list[dict[str, Any]]:
        self.raw_text += delta
        events = self.thinking.feed(self.raw_text) if self.thinking_enabled else []
        body_text = self._body_text()
        if body_text is None:
            return events
        events += self.blocks.feed(body_text)
        return events

    def _body_text(self) -> str | None:
        if not self.thinking_enabled:
            return self.raw_text
        if self.thinking.started and not self.thinking.done:
            return None
        if self.thinking.done and self.thinking.close_end is not None:
            return self.raw_text[self.thinking.close_end :]
        return self.raw_text


def _extract_complete_block(text: str, tag_name: str) -> str | None:
    match = re.search(fr"<{tag_name}\b[^>]*>", text)
    if not match:
        return None
    close_tag = f"</{tag_name}>"
    close_index = text.find(close_tag, match.end())
    if close_index == -1:
        return None
    return text[match.start() : close_index + len(close_tag)]


def _extract_all_complete_blocks(text: str, tag_name: str) -> list[str]:
    blocks: list[str] = []
    search_pos = 0
    while True:
        match = re.search(fr"<{tag_name}\b[^>]*>", text[search_pos:])
        if not match:
            return blocks
        start = search_pos + match.start()
        close_tag = f"</{tag_name}>"
        close_index = text.find(close_tag, search_pos + match.end())
        if close_index == -1:
            return blocks
        blocks.append(text[start : close_index + len(close_tag)])
        search_pos = close_index + len(close_tag)


def _extract_top_level_complete_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    search_pos = 0
    while True:
        match = re.search(r"<([A-Za-z_][\w.-]*)\b[^>]*>", text[search_pos:], flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return blocks
        start = search_pos + match.start()
        open_end = search_pos + match.end()
        close_span = _find_close_tag_span(text, match.group(1), open_end)
        if not close_span:
            return blocks
        blocks.append(text[start : close_span[1]])
        search_pos = close_span[1]


def _find_close_tag_span(text: str, tag_name: str, start: int) -> tuple[int, int] | None:
    match = re.search(fr"</{re.escape(tag_name)}\s*>", text[start:], flags=re.IGNORECASE)
    if not match:
        return None
    return start + match.start(), start + match.end()


def _strip_leading_thinking_block(text: str) -> str:
    cleaned = text
    pattern = re.compile(r"^\s*<(think|thinking)\b[^>]*>.*?</\1>\s*", flags=re.IGNORECASE | re.DOTALL)
    while True:
        updated = pattern.sub("", cleaned, count=1)
        if updated == cleaned:
            return cleaned.strip()
        cleaned = updated.strip()


def _prepare_protocol_text(text: str) -> str:
    cleaned = text.strip().strip("```xml").strip("```").strip()
    cleaned = re.sub(r"<\?xml[^>]*\?>", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = _strip_leading_thinking_block(cleaned)
    return _repair_pseudo_kv_xml(cleaned)


def _extract_tag_inner_text(text: str, tag_name: str) -> str:
    match = re.search(fr"<{tag_name}\b[^>]*>(.*?)</{tag_name}>", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return match.group(1).strip()


def _extract_unclosed_tag_text(text: str, tag_name: str) -> str:
    match = re.search(fr"<{tag_name}\b[^>]*>", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    tail = text[match.end() :]
    next_tag = re.search(r"</?[A-Za-z_][\w.-]*\b[^>]*>", tail, flags=re.IGNORECASE | re.DOTALL)
    return tail[: next_tag.start()].strip() if next_tag else tail.strip()


def _extract_start_tag(text: str) -> str:
    match = re.search(r"<[A-Za-z_][\w.-]*\b[^>]*>", text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(0) if match else ""


def _extract_start_tag_name(text: str) -> str:
    match = re.search(r"<([A-Za-z_][\w.-]*)\b[^>]*>", text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else ""


def _extract_attribute(text: str, tag_name: str, attribute_name: str) -> str:
    start_tag = re.search(fr"<{tag_name}\b[^>]*>", text, flags=re.IGNORECASE | re.DOTALL)
    if not start_tag:
        return ""
    return _extract_attribute_from_start_tag(start_tag.group(0), attribute_name)


def _extract_attribute_from_start_tag(start_tag: str, attribute_name: str) -> str:
    # LLMs often mix XML-ish single quotes or curly quotes; accept all common quoted forms.
    quote_chars = "\"'‘’"
    match = re.search(
        fr"\b{re.escape(attribute_name)}\s*=\s*([{re.escape(quote_chars)}])([^{re.escape(quote_chars)}]*)[{re.escape(quote_chars)}]",
        start_tag,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return match.group(2)


def _extract_attributes_from_start_tag(start_tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    quote_chars = "\"'‘’"
    for match in re.finditer(
        fr"\b([A-Za-z_][\w.-]*)\s*=\s*([{re.escape(quote_chars)}])([^{re.escape(quote_chars)}]*)[{re.escape(quote_chars)}]",
        start_tag,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        attrs[match.group(1)] = match.group(3)
    return attrs


def _repair_pseudo_kv_xml(text: str) -> str:
    return re.sub(r"(?m)^(\s*)<([A-Za-z_][\w.]*)[ \t]*:[ \t]*(.*)$", r"\1\2: \3", text)


def _strip_surrounding_angle_brackets(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("<") and ":" in stripped and not stripped.endswith(">"):
        return stripped[1:]
    if stripped.startswith("<") and stripped.endswith(">") and ":" in stripped:
        return stripped[1:-1]
    return line


def _strip_protocol_tags(text: str) -> str:
    cleaned = re.sub(r"</?[^>]+>", "", text)
    return re.sub(r"<[^>\n]*$", "", cleaned)


def _assign_nested(target: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = [part.strip() for part in dotted_key.split(".") if part.strip()]
    if not parts:
        return
    cursor = target
    for part in parts[:-1]:
        existing = cursor.get(part)
        if not isinstance(existing, dict):
            existing = {}
            cursor[part] = existing
        cursor = existing
    cursor[parts[-1]] = value


def _coerce_scalar(value: str) -> Any:
    if value == "":
        return ""
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        if value.startswith("{") or value.startswith("["):
            return json.loads(value)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


def _coerce_value_with_reason(raw_value: str) -> Any:
    if "|" not in raw_value:
        return _coerce_scalar(raw_value)
    value_text, reason_text = raw_value.split("|", 1)
    reason = reason_text.strip()
    if not reason:
        return _coerce_scalar(value_text.strip())
    return {
        "value": _coerce_scalar(value_text.strip()),
        "reason": reason,
    }
