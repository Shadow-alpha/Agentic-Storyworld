from __future__ import annotations

import json
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


class StateManager:
    """Owns all runtime state reads and writes."""

    CHECKPOINT_STATUSES = ("unstarted", "available", "in_progress", "completed")
    RECENT_TURN_MEMORY_LIMIT = 5
    STATIC_WORLD_KEYS = {"world_id", "map_locations", "organizations", "description", "summary"}
    STATIC_USER_KEYS = {"name"}

    def __init__(self, game_root: Path, user_game_root: Path | None = None) -> None:
        self.game_root = game_root
        self.base_dir = game_root / "base"
        self.user_game_root = user_game_root or game_root
        self.runtime_dir = self.user_game_root / "runtime"
        self.saves_dir = self.user_game_root / "saves"
        self.runtime_state: dict[str, Any] | None = None
        self.logs: dict[str, list[dict[str, Any]]] = {"turn_log": []}
        self.static_state: dict[str, Any] = self._load_static_state()
        self._base_template_cache: dict[str, Any] = {
            "user_state": self.static_state.get("user_state", {}),
            "world_state": self.static_state.get("world_state", {}),
            "character_state": self.static_state.get("character_states", {}),
            "goals": self.static_state.get("goals", {}),
        }

    def initialize_runtime(self, reset: bool = False) -> None:
        """Create runtime if needed, then load runtime state and logs once."""
        if reset and self.runtime_dir.exists():
            shutil.rmtree(self.runtime_dir)
        if self.runtime_dir.exists():
            self._ensure_runtime_logs()
            if not (self.runtime_dir / "state.json").exists():
                self._write_json(self.runtime_dir / "state.json", self._initial_runtime_state())
                self._ensure_runtime_memory_files()
        else:
            self._create_runtime()
        self.runtime_state = self._compose_runtime_state()
        self.logs = self._load_logs()
        self._ensure_goal_runtime_fields()
        self._write_runtime_state()

    def get_runtime_state(self) -> dict[str, Any]:
        """Return the raw runtime state source of truth."""
        if self.runtime_state is None:
            self.initialize_runtime()
        return self.runtime_state or {}

    def get_agent_state_view(self) -> dict[str, Any]:
        """Return a normalized state view for Director and Character agents."""
        runtime_state = self.get_runtime_state()
        state_view = self._build_state_view(runtime_state)
        state_view["config"] = self._without_display_names(state_view.get("config", {}))
        state_view["goals"] = self._build_agent_goals_view(runtime_state.get("goals", {}))
        world_state = state_view.get("world_state", {})
        map_locations = {}
        if isinstance(world_state, dict):
            map_locations = world_state.get("map_locations", {})
            if not isinstance(map_locations, dict):
                map_locations = {}
        user_state = state_view.get("user_state", {})
        if isinstance(user_state, dict):
            if runtime_state.get("player_profile"):
                user_state["player_profile"] = runtime_state["player_profile"]
            user_location = user_state.get("location")
            if user_location in map_locations and isinstance(map_locations[user_location], dict):
                user_state["location"] = {"id": user_location, **deepcopy(map_locations[user_location])}
            user_state["stats"] = self._build_agent_stat_view(state_view)
            user_state["relations"] = self._build_user_character_relations(
                state_view.get("characters", {}),
                state_view.get("config", {}),
            )
            user_state["possessions"] = self._expand_possessions(
                user_state.get("possessions", []),
                state_view.get("config", {}),
            )
        for character_id, character in state_view.get("characters", {}).items():
            character["memory"] = self._build_agent_memory_view(character.get("memory", ""))
            character_state = character.get("state", {})
            if isinstance(character_state, dict):
                character_location = character_state.get("location")
                if character_location in map_locations and isinstance(map_locations[character_location], dict):
                    character_state["location"] = {"id": character_location, **deepcopy(map_locations[character_location])}
                character_state["stats"] = self._build_character_agent_stat_view(
                    character_state,
                    state_view.get("config", {}),
                )
                relation_rules = state_view.get("config", {}).get("relation_rules", {})
                character_state["relations"] = self._compact_stats(relation_rules, character_state.get("relations", {}))
                character_state["possessions"] = self._expand_possessions(
                    character_state.get("possessions", []),
                    state_view.get("config", {}),
                )
        return state_view

    def get_ui_state_view(self) -> dict[str, Any]:
        """Return a normalized state view for frontend rendering."""
        runtime_state = self.get_runtime_state()
        state_view = self._build_state_view(runtime_state)
        state_view["goals"] = self._build_ui_goals_view(runtime_state.get("goals", {}))
        state_view["player_display_name"] = self._player_display_name(runtime_state.get("player_profile", ""))
        state_view["player_profile"] = runtime_state.get("player_profile", "")
        state_view["stat_rules"] = state_view.get("config", {}).get("stat_rules", {})
        return state_view

    def get_logs(self, limit: int | None = 3) -> dict[str, list[dict[str, Any]]]:
        """Return cached recent turn logs."""
        return {
            name: records[-limit:] if limit is not None else list(records)
            for name, records in self.logs.items()
        }

    def get_turn_count(self) -> int:
        """Return the number of completed turns in cached turn logs."""
        return len(self.logs.get("turn_log", []))

    def apply_state_update(self, update: dict[str, Any]) -> dict[str, Any]:
        """Apply model-produced updates, save files, and return actual changes."""
        changes: dict[str, Any] = {"world_state": {}, "user_state": {}, "characters": {}}
        if not isinstance(update, dict) or not update:
            return changes

        runtime_state = self.get_runtime_state()
        if "world_state" in update:
            changes["world_state"] = self._apply_world_update(runtime_state, update["world_state"])
        if "user_state" in update:
            changes["user_state"] = self._apply_user_update(runtime_state, update["user_state"])
        changes["characters"] = self._apply_character_updates(runtime_state, update.get("characters", {}))
        return changes

    def apply_goal_update(self, goal_update: dict[str, Any]) -> dict[str, Any]:
        """Update active goal checkpoints, then refresh graph-based goal state."""
        result: dict[str, Any] = {
            "checkpoints": [],
            "completed_goals": [],
            "available_goals": [],
        }
        if not isinstance(goal_update, dict):
            return result

        runtime_state = self.get_runtime_state()
        goals_config = runtime_state.setdefault("goals", {})
        if not isinstance(goals_config, dict):
            return result

        goal_definitions = self._goal_definitions()
        if not isinstance(goal_definitions, dict):
            return result

        self._ensure_goal_runtime_fields()
        active_goals = goals_config.setdefault("active_goals", {})
        if not isinstance(active_goals, dict):
            return result
        active_goal_ids = set(active_goals)

        for item in self._iter_goal_update_items(goal_update):
            if not isinstance(item, dict):
                continue
            goal_id = str(item.get("goal_id", "")).strip()
            checkpoint_id = str(item.get("checkpoint_id", "")).strip()
            status = self._normalize_checkpoint_status(item.get("status", "completed"))
            if not self._is_valid_active_checkpoint(goal_definitions, active_goal_ids, goal_id, checkpoint_id):
                continue
            checkpoint = active_goals.setdefault(goal_id, {}).setdefault(checkpoint_id, {"status": "unstarted"})
            if not checkpoint or not self._can_advance_checkpoint(checkpoint.get("status"), status):
                continue

            note = str(item.get("progress_note") or item.get("evidence") or "").strip()
            checkpoint["status"] = status
            if note:
                checkpoint["progress_note"] = note
            accepted = {
                "goal_id": goal_id,
                "checkpoint_id": checkpoint_id,
                "status": status,
                "progress_note": note,
            }
            result["checkpoints"].append(accepted)

        result.update(self._refresh_goal_graph_state(goal_definitions, goals_config))

        if result["checkpoints"] or result["completed_goals"] or result["available_goals"]:
            self._write_runtime_state()
        return result

    def activate_goal(self, goal_id: str) -> dict[str, Any]:
        """Move an available goal into active goals."""
        return self._move_goal_between_lists(goal_id, source="available_goals", target="active_goals")

    def deactivate_goal(self, goal_id: str) -> dict[str, Any]:
        """Move an active unfinished goal back into available goals."""
        return self._move_goal_between_lists(goal_id, source="active_goals", target="available_goals")

    def check_endings(self) -> dict[str, Any]:
        """Return and persist the highest-priority ending whose requirements are met."""
        runtime_state = self.get_runtime_state()
        goals_config = runtime_state.setdefault("goals", {})
        if not isinstance(goals_config, dict):
            return {}

        existing = goals_config.get("ending_state", {})
        if isinstance(existing, dict) and existing.get("is_ended"):
            return existing

        endings = self.static_state.get("goals", {}).get("endings", {})
        if not isinstance(endings, dict):
            return {}

        sorted_endings = sorted(
            endings.items(),
            key=lambda item: item[1].get("priority", 0) if isinstance(item[1], dict) else 0,
            reverse=True,
        )
        for ending_id, ending in sorted_endings:
            if not isinstance(ending, dict):
                continue
            if self._ending_required_met(runtime_state, ending.get("required", {})):
                ending_state = {
                    "is_ended": True,
                    "ending_id": ending_id,
                    "title": ending.get("title", ending_id),
                    "description": ending.get("description", ""),
                    "priority": ending.get("priority", 0),
                }
                goals_config["ending_state"] = ending_state
                self._write_runtime_state()
                return ending_state
        return {}

    def apply_player_customization(self, values: dict[str, Any]) -> dict[str, Any]:
        """Persist opening player customization as a text profile."""
        if not isinstance(values, dict):
            return {}
        runtime_state = self.get_runtime_state()
        config = runtime_state.get("config", {})
        allowed_fields = config.get("player_customization", {}) if isinstance(config, dict) else {}
        if not isinstance(allowed_fields, dict) or not allowed_fields:
            return {}

        accepted: dict[str, Any] = {}
        for field_name, field_config in allowed_fields.items():
            if field_name not in values:
                continue
            value = values.get(field_name)
            field_type = field_config.get("type") if isinstance(field_config, dict) else ""
            if field_type == "number":
                try:
                    number = float(value)
                    value = int(number) if number.is_integer() else number
                except (TypeError, ValueError):
                    pass
            elif isinstance(value, str):
                value = value.strip()
            accepted[field_name] = value

        if accepted:
            profile_text = self._build_player_profile_text(accepted, allowed_fields)
            runtime_state["player_profile"] = profile_text
            (self.runtime_dir / "player_profile.txt").write_text(profile_text, encoding="utf-8")
        return accepted

    def append_log(self, turn_record: dict[str, Any]) -> None:
        """Append one complete turn record to memory and the JSONL log."""
        if not isinstance(turn_record, dict):
            return
        self._append_log_record("turn_log", turn_record)

    def save(self, slot_id: str) -> None:
        """Persist the current runtime directory into a save slot."""
        slot_dir = self.saves_dir / slot_id
        runtime_target = slot_dir / "runtime"
        slot_dir.mkdir(parents=True, exist_ok=True)
        if runtime_target.exists():
            shutil.rmtree(runtime_target)
        shutil.copytree(self.runtime_dir, runtime_target)
        self._write_json(
            slot_dir / "meta.json",
            {"saved_at": datetime.utcnow().isoformat(timespec="seconds") + "Z"},
        )

    def load(self, slot_id: str) -> None:
        """Replace runtime with the contents of a previously saved slot."""
        slot_dir = self.saves_dir / slot_id / "runtime"
        if not slot_dir.exists():
            raise FileNotFoundError(f"Save slot not found: {slot_id}")
        if self.runtime_dir.exists():
            shutil.rmtree(self.runtime_dir)
        shutil.copytree(slot_dir, self.runtime_dir)
        self.initialize_runtime()

    def list_saves(self) -> list[dict[str, Any]]:
        """List available save slots and their metadata."""
        if not self.saves_dir.exists():
            return []
        saves = []
        for slot_dir in sorted(p for p in self.saves_dir.iterdir() if p.is_dir()):
            meta = self._read_json(slot_dir / "meta.json", default={})
            saves.append(
                {
                    "slot_id": slot_dir.name,
                    "saved_at": meta.get("saved_at"),
                }
            )
        return saves

    def _apply_world_update(self, runtime_state: dict[str, Any], delta: Any) -> dict[str, dict[str, str]]:
        """Apply world_state updates that match the runtime file structure."""
        world_state = runtime_state.setdefault("world_state", {})
        world_delta = self._normalize("world_state", delta)
        changes: dict[str, dict[str, str]] = {}
        self._merge_known_fields(world_state, world_delta, changes)
        if changes:
            self._write_runtime_state()
        return changes

    def _apply_user_update(self, runtime_state: dict[str, Any], delta: Any) -> dict[str, dict[str, str]]:
        """Apply user_state updates after normalization and stat-rule clamping."""
        user_state = runtime_state.setdefault("user_state", {})
        user_delta = self._normalize("user_state", delta)
        user_delta = self._apply_state_rules("user_state", user_delta, user_state)
        changes: dict[str, dict[str, str]] = {}
        self._merge_known_fields(user_state, user_delta, changes)
        if changes:
            self._write_runtime_state()
        return changes

    def _apply_character_updates(self, runtime_state: dict[str, Any], updates: Any) -> dict[str, dict[str, dict[str, str]]]:
        """Apply character state/memory updates for known base-template characters."""
        changes: dict[str, dict[str, dict[str, str]]] = {}
        if not isinstance(updates, dict):
            return changes
        runtime_characters = runtime_state.setdefault("characters", {})
        for character_id, update in updates.items():
            if not isinstance(update, dict):
                continue
            if not self._get_base_template("character_state", character_id=character_id):
                continue

            character = runtime_characters.get(character_id)
            if not isinstance(character, dict):
                continue

            character_state = character.setdefault("state", {})
            character_changes: dict[str, dict[str, str]] = {}
            emotion = str(update.get("emotion", "") or "").strip()
            if emotion and character_state.get("emotion") != emotion:
                old_emotion = character_state.get("emotion")
                character_state["emotion"] = emotion
                self._record_change(character_changes, ("emotion",), old_emotion, emotion)

            character_delta = self._normalize(
                "character_state",
                self._filter_character_state_update(update.get("state_update", {}), character_id),
                character_id=character_id,
            )
            character_delta = self._apply_state_rules("character_state", character_delta, character_state)
            self._merge_known_fields(character_state, character_delta, character_changes)
            if character_changes:
                changes[character_id] = character_changes

            memory_append = update.get("memory_append")
            if memory_append:
                cleaned_memory = self._clean_memory_append(memory_append)
                if cleaned_memory:
                    existing = character.get("memory", "")
                    turn_number = self.get_turn_count() + 1
                    entry = f"## Turn {turn_number}\n\n{cleaned_memory}\n"
                    updated = existing.rstrip() + ("\n\n" if existing.strip() else "") + entry
                    character["memory"] = updated
                    memory_path = self.runtime_dir / "memory" / f"{character_id}.md"
                    memory_path.parent.mkdir(parents=True, exist_ok=True)
                    memory_path.write_text(updated, encoding="utf-8")
        if changes:
            self._write_runtime_state()
        return changes

    def _filter_character_state_update(self, delta: Any, character_id: str) -> dict[str, Any]:
        """Keep only character stats/relations updates from the state_update block."""
        if not isinstance(delta, dict):
            return {}
        template = self._get_base_template("character_state", character_id=character_id)
        allowed = {"location", "stats", "relations"}
        for group_name in ("stats", "relations"):
            group_template = template.get(group_name, {}) if isinstance(template, dict) else {}
            if isinstance(group_template, dict):
                allowed.update(group_template.keys())
        return {key: value for key, value in delta.items() if key in allowed}

    def _load_logs(self) -> dict[str, list[dict[str, Any]]]:
        """Load runtime JSONL turn logs into memory."""
        logs_dir = self.runtime_dir / "logs"
        return {
            "turn_log": self._read_jsonl(logs_dir / "turn_log.jsonl"),
        }

    def _clean_memory_append(self, memory_append: Any) -> str:
        """Keep only supported memory lines before writing MEMORY.md."""
        lines: list[str] = []
        for raw_line in str(memory_append or "").splitlines():
            line = raw_line.strip()
            lowered = line.lower()
            if lowered.startswith("[turn]"):
                text = line[6:].strip()
                if text:
                    lines.append(f"[turn] {text}")
            elif lowered.startswith("[core]"):
                text = line[6:].strip()
                if text:
                    lines.append(f"[core] {text}")
        return "\n".join(lines)

    def _build_agent_memory_view(self, memory_text: Any) -> dict[str, list[dict[str, Any]]]:
        """Return core memories and recent turn memories for character prompts."""
        parsed = self._parse_character_memory(memory_text)
        return {
            "core": parsed["core"],
            "turns": parsed["turns"][-self.RECENT_TURN_MEMORY_LIMIT:],
        }

    def _parse_character_memory(self, memory_text: Any) -> dict[str, list[dict[str, Any]]]:
        """Parse MEMORY.md turn headers and [turn]/[core] lines."""
        parsed: dict[str, list[dict[str, Any]]] = {"core": [], "turns": []}
        current_turn: int | None = None
        for raw_line in str(memory_text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.lower().startswith("## turn"):
                current_turn = self._parse_turn_number(line)
                continue
            lowered = line.lower()
            if lowered.startswith("[core]"):
                text = line[6:].strip()
                if text:
                    parsed["core"].append({"turn": current_turn, "text": text})
            elif lowered.startswith("[turn]"):
                text = line[6:].strip()
                if text:
                    parsed["turns"].append({"turn": current_turn, "text": text})
        return parsed

    def _parse_turn_number(self, line: str) -> int | None:
        """Extract the numeric turn from a MEMORY.md header."""
        for token in line.replace("#", " ").split():
            if token.isdigit():
                return int(token)
        return None

    def _append_log_record(self, log_name: str, record: dict[str, Any]) -> None:
        """Append one log record to the in-memory cache and JSONL file."""
        payload = dict(record)
        payload.setdefault("timestamp", datetime.utcnow().isoformat(timespec="seconds") + "Z")
        self.logs.setdefault(log_name, []).append(payload)
        self._append_jsonl(self.runtime_dir / "logs" / f"{log_name}.jsonl", payload)

    def _load_static_state(self) -> dict[str, Any]:
        """Load static base data once for runtime-state composition."""
        characters_dir = self.base_dir / "characters"
        character_states: dict[str, Any] = {}
        character_profiles: dict[str, str] = {}
        if characters_dir.exists():
            for character_dir in sorted(p for p in characters_dir.iterdir() if p.is_dir()):
                character_states[character_dir.name] = self._read_json(character_dir / "state.json", default={})
                character_profiles[character_dir.name] = self._read_text(character_dir / "PROFILE.md")
        return {
            "config": self._read_json(self.base_dir / "config.json", default={}),
            "user_state": self._read_json(self.base_dir / "user_state.json", default={}),
            "world_state": self._read_json(self.base_dir / "world_state.json", default={}),
            "character_states": character_states,
            "character_profiles": character_profiles,
            "goals": self._read_json(self.base_dir / "goals.json", default={}),
        }

    def _get_base_template(self, kind: str, character_id: str | None = None) -> dict[str, Any]:
        """Return the matching base JSON template for a given state kind."""
        if kind == "character_state":
            templates = self._base_template_cache.get("character_state", {})
            if character_id:
                return templates.get(character_id, {})
            return next(iter(templates.values()), {})
        return self._base_template_cache.get(kind, {})

    def _compose_runtime_state(self) -> dict[str, Any]:
        """Compose static base data with runtime/state.json dynamic state."""
        state_data = self._read_json(self.runtime_dir / "state.json", default={})
        if not isinstance(state_data, dict):
            state_data = {}

        character_profiles = self.static_state.get("character_profiles", {})
        character_states = self.static_state.get("character_states", {})
        runtime_characters = state_data.get("characters", {})
        if not isinstance(runtime_characters, dict):
            runtime_characters = {}
        characters: dict[str, Any] = {}
        if isinstance(character_states, dict):
            for character_id, base_character_state in character_states.items():
                memory_path = self.runtime_dir / "memory" / f"{character_id}.md"
                runtime_character = runtime_characters.get(character_id, {})
                runtime_character_state = runtime_character.get("state", {}) if isinstance(runtime_character, dict) else {}
                characters[character_id] = {
                    "profile": character_profiles.get(character_id, "") if isinstance(character_profiles, dict) else "",
                    "state": deepcopy(runtime_character_state or base_character_state),
                    "memory": self._read_text(memory_path),
                }
        return {
            "config": deepcopy(self.static_state.get("config", {})),
            "goals": deepcopy(state_data.get("goals") or self._initial_runtime_goals()),
            "world_state": self._compose_world_state(
                self.static_state.get("world_state", {}),
                state_data.get("world_state", {}),
            ),
            "user_state": self._extract_runtime_user_state(state_data.get("user_state") or self.static_state.get("user_state", {})),
            "characters": characters,
            "player_profile": self._read_text(self.runtime_dir / "player_profile.txt"),
        }

    def _build_player_profile_text(self, accepted: dict[str, Any], allowed_fields: dict[str, Any]) -> str:
        """Format player customization values for agent prompts."""
        lines = []
        for field_name, field_config in allowed_fields.items():
            if field_name not in accepted:
                continue
            label = field_config.get("label", field_name) if isinstance(field_config, dict) else field_name
            lines.append(f"{label}:{accepted[field_name]}")
        return "\n".join(lines)

    def _create_runtime(self) -> None:
        """Create a fresh lightweight runtime tree."""
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(self.runtime_dir / "state.json", self._initial_runtime_state())
        self._ensure_runtime_memory_files()
        self._ensure_runtime_logs()
        self.runtime_state = None
        self.logs = {"turn_log": []}

    def _initial_runtime_state(self) -> dict[str, Any]:
        """Build the dynamic runtime payload from base state templates."""
        characters: dict[str, Any] = {}
        character_states = self.static_state.get("character_states", {})
        if isinstance(character_states, dict):
            for character_id, character_state in character_states.items():
                characters[character_id] = {"state": deepcopy(character_state)}
        return {
            "goals": self._initial_runtime_goals(),
            "world_state": self._extract_runtime_world_state(self.static_state.get("world_state", {})),
            "user_state": self._extract_runtime_user_state(self.static_state.get("user_state", {})),
            "characters": characters,
        }

    def _write_runtime_state(self) -> None:
        """Persist the dynamic runtime state to runtime/state.json."""
        runtime_state = self.get_runtime_state()
        characters: dict[str, Any] = {}
        for character_id, character in (runtime_state.get("characters", {}) or {}).items():
            if isinstance(character, dict):
                characters[character_id] = {"state": deepcopy(character.get("state", {}))}
        self._write_json(
            self.runtime_dir / "state.json",
            {
                "goals": deepcopy(runtime_state.get("goals", {})),
                "world_state": self._extract_runtime_world_state(runtime_state.get("world_state", {})),
                "user_state": self._extract_runtime_user_state(runtime_state.get("user_state", {})),
                "characters": characters,
            },
        )

    def _compose_world_state(self, base_world_state: Any, runtime_world_state: Any) -> dict[str, Any]:
        """Combine static base world data with runtime-only world fields."""
        merged = deepcopy(base_world_state) if isinstance(base_world_state, dict) else {}
        if isinstance(runtime_world_state, dict):
            for key, value in runtime_world_state.items():
                merged[key] = deepcopy(value)
        return merged

    def _extract_runtime_world_state(self, world_state: Any) -> dict[str, Any]:
        """Keep only dynamic world fields in runtime/state.json."""
        if not isinstance(world_state, dict):
            return {}
        return {
            key: deepcopy(value)
            for key, value in world_state.items()
            if key not in self.STATIC_WORLD_KEYS
        }

    def _extract_runtime_user_state(self, user_state: Any) -> dict[str, Any]:
        """Keep only runtime user-state fields."""
        if not isinstance(user_state, dict):
            return {}
        return {
            key: deepcopy(value)
            for key, value in user_state.items()
            if key not in self.STATIC_USER_KEYS
        }

    def _initial_runtime_goals(self) -> dict[str, Any]:
        """Build initial runtime goal buckets from base goal_state or root goals."""
        base_goals = self.static_state.get("goals", {})
        definitions = base_goals.get("goals", {}) if isinstance(base_goals, dict) else {}
        configured = base_goals.get("goal_state", {}) if isinstance(base_goals, dict) else {}
        if not isinstance(definitions, dict):
            definitions = {}
        if not isinstance(configured, dict):
            configured = {}

        active_ids = configured.get("active_goals")
        if not isinstance(active_ids, list):
            active_ids = [
                goal_id
                for goal_id, goal in definitions.items()
                if isinstance(goal, dict) and not goal.get("unlock_condition") and not self._predecessor_goal_ids(definitions, goal_id)
            ]
        available_ids = configured.get("available_goals", [])
        completed_ids = configured.get("completed_goals", [])
        return {
            "active_goals": self._goal_bucket_from_ids(definitions, active_ids),
            "available_goals": self._goal_bucket_from_ids(definitions, available_ids),
            "completed_goals": list(completed_ids) if isinstance(completed_ids, list) else [],
        }

    def _goal_bucket_from_ids(self, goals: dict[str, Any], goal_ids: Any) -> dict[str, Any]:
        """Return runtime checkpoint state for each known goal id."""
        if not isinstance(goal_ids, list):
            return {}
        return {
            goal_id: self._extract_goal_checkpoint_state(goals[goal_id])
            for goal_id in goal_ids
            if goal_id in goals
        }

    def _goal_definitions(self) -> dict[str, Any]:
        """Return static goal definitions from base goals.json."""
        base_goals = self.static_state.get("goals", {})
        definitions = base_goals.get("goals", {}) if isinstance(base_goals, dict) else {}
        return definitions if isinstance(definitions, dict) else {}

    def _extract_goal_checkpoint_state(self, goal: Any) -> dict[str, Any]:
        """Extract checkpoint runtime status for one goal."""
        checkpoints = goal.get("checkpoints", []) if isinstance(goal, dict) else []
        if not isinstance(checkpoints, list):
            return {}
        state: dict[str, Any] = {}
        for checkpoint in checkpoints:
            if not isinstance(checkpoint, dict) or not checkpoint.get("id"):
                continue
            item = {"status": self._normalize_checkpoint_status(checkpoint.get("status", "unstarted"))}
            if checkpoint.get("progress_note"):
                item["progress_note"] = checkpoint.get("progress_note")
            state[str(checkpoint["id"])] = item
        return state

    def _ensure_runtime_memory_files(self) -> None:
        """Create runtime memory files from base character memories when missing."""
        memory_dir = self.runtime_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        character_states = self.static_state.get("character_states", {})
        if not isinstance(character_states, dict):
            return
        for character_id in sorted(character_states):
            memory_path = memory_dir / f"{character_id}.md"
            if not memory_path.exists():
                memory_path.write_text(
                    self._read_text(self.base_dir / "characters" / character_id / "MEMORY.md"),
                    encoding="utf-8",
                )

    def _ensure_runtime_logs(self) -> None:
        """Ensure runtime log files exist before appending new records."""
        logs_dir = self.runtime_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "turn_log.jsonl").touch(exist_ok=True)

    def _read_json(self, path: Path, default: Any) -> Any:
        """Read JSON from disk, returning a default value when missing."""
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, data: Any) -> None:
        """Write JSON data to disk using UTF-8 and stable indentation."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_text(self, path: Path) -> str:
        """Read a UTF-8 text file, returning an empty string when missing."""
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _append_jsonl(self, path: Path, record: dict[str, Any]) -> None:
        """Append one JSON record to a JSONL log file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(record)
        payload.setdefault("timestamp", datetime.utcnow().isoformat(timespec="seconds") + "Z")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _read_jsonl(self, path: Path, limit: int | None = None) -> list[dict[str, Any]]:
        """Read recent JSONL records, optionally limiting to the last N items."""
        if not path.exists():
            return []
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if limit is not None:
            lines = lines[-limit:]
        return [json.loads(line) for line in lines]

    def _merge_known_fields(
        self,
        base: dict[str, Any],
        delta: dict[str, Any],
        changes: dict[str, dict[str, str]],
        path: tuple[str, ...] = (),
    ) -> None:
        """Recursively update only fields that already exist in the target state."""
        for key, value in delta.items():
            if key not in base:
                continue
            base_value = base.get(key)
            if isinstance(base_value, dict) and {"value", "description"} & set(base_value.keys()):
                old_value = base_value.get("value")
                reason = self._extract_update_reason(value)
                if isinstance(value, dict):
                    if "value" in value:
                        base_value["value"] = self._extract_update_value(value)
                    if "description" in value and "description" in base_value:
                        base_value["description"] = value["description"]
                else:
                    base_value["value"] = value
                self._record_change(changes, path + (key, "value"), old_value, base_value.get("value"), reason)
                continue
            if isinstance(base_value, dict) and isinstance(value, dict):
                if "value" in value and len(value.keys() - {"value", "reason"}) == 0:
                    value = self._extract_update_value(value)
                else:
                    self._merge_known_fields(base_value, value, changes, path + (key,))
                    continue
            reason = self._extract_update_reason(value)
            next_value = self._extract_update_value(value)
            old_value = base.get(key)
            base[key] = next_value
            self._record_change(changes, path + (key,), old_value, next_value, reason)

    def _extract_update_value(self, value: Any) -> Any:
        """Return the actual state value from parser output that may include a reason."""
        if isinstance(value, dict) and "value" in value:
            return value.get("value")
        return value

    def _extract_update_reason(self, value: Any) -> str:
        """Return the optional state update reason from parser output."""
        if isinstance(value, dict):
            return str(value.get("reason", "") or "").strip()
        return ""

    def _record_change(
        self,
        changes: dict[str, dict[str, str]],
        path: tuple[str, ...],
        before: Any,
        after: Any,
        reason: str = "",
    ) -> None:
        """Record one changed field using a compact display key."""
        if before == after:
            return
        changes[self._change_key(path)] = {
            "change": f"{self._format_change_value(before)} -> {self._format_change_value(after)}",
            "reason": reason,
        }

    def _change_key(self, path: tuple[str, ...]) -> str:
        """Convert internal nested state paths into compact log keys."""
        return ".".join(path)

    def _format_change_value(self, value: Any) -> str:
        """Format scalar values for compact state-change logs."""
        if isinstance(value, dict) and "value" in value:
            value = value.get("value")
        if value is None:
            return "未知"
        return str(value)

    def _apply_state_rules(
        self,
        kind: str,
        delta: dict[str, Any],
        current_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Clamp configured stat/relation updates before merging into runtime."""
        if not isinstance(delta, dict):
            return {}
        config = self.get_runtime_state().get("config", {})
        stat_rules = config.get("stat_rules", {}) if isinstance(config, dict) else {}
        if not isinstance(stat_rules, dict):
            return delta

        normalized = deepcopy(delta)
        self._clamp_stats_delta(normalized, current_state, stat_rules)

        if kind == "character_state":
            self._clamp_relations_delta(
                normalized,
                current_state,
                config.get("relation_rules", {}) if isinstance(config, dict) else {},
            )
        return normalized

    def _clamp_stats_delta(
        self,
        delta: dict[str, Any],
        current_state: dict[str, Any],
        stat_rules: Any,
    ) -> None:
        """Clamp stats.*.value updates using configured range and max delta."""
        stats_delta = delta.get("stats")
        if not isinstance(stats_delta, dict) or not isinstance(stat_rules, dict):
            return
        current_stats = current_state.get("stats", {})
        if not isinstance(current_stats, dict):
            current_stats = {}
        for stat_name, stat_delta in list(stats_delta.items()):
            if not isinstance(stat_delta, dict) or "value" not in stat_delta:
                continue
            rule = stat_rules.get(stat_name, {})
            current_entry = current_stats.get(stat_name, {})
            current_value = current_entry.get("value") if isinstance(current_entry, dict) else current_entry
            stat_delta["value"] = self._clamp_rule_value(stat_delta.get("value"), current_value, rule)

    def _clamp_relations_delta(
        self,
        delta: dict[str, Any],
        current_state: dict[str, Any],
        relation_rule: Any,
    ) -> None:
        """Clamp relation updates using shared relation rules."""
        relations_delta = delta.get("relations")
        if not isinstance(relations_delta, dict):
            return
        current_relations = current_state.get("relations", {})
        if not isinstance(current_relations, dict):
            current_relations = {}
        if not isinstance(relation_rule, dict):
            relation_rule = {}
        for key, value in list(relations_delta.items()):
            relations_delta[key] = self._clamp_rule_value(value, current_relations.get(key), relation_rule.get(key, {}))

    def _clamp_rule_value(self, value: Any, current_value: Any, rule: Any) -> Any:
        """Apply numeric range and max-delta constraints when configured."""
        if isinstance(value, dict) and "value" in value:
            clamped = dict(value)
            clamped["value"] = self._clamp_rule_value(value.get("value"), current_value, rule)
            return clamped
        value = self._coerce_delta_value(value)
        if not isinstance(rule, dict):
            return value
        try:
            next_value = float(value)
        except (TypeError, ValueError):
            return value

        value_range = rule.get("range")
        if isinstance(value_range, list) and len(value_range) == 2:
            try:
                lower = float(value_range[0])
                upper = float(value_range[1])
                next_value = max(lower, min(upper, next_value))
            except (TypeError, ValueError):
                pass

        try:
            current_number = float(current_value)
            max_delta = float(rule.get("max_delta_per_turn"))
        except (TypeError, ValueError):
            max_delta = None
        if max_delta is not None and max_delta >= 0:
            lower = current_number - max_delta
            upper = current_number + max_delta
            next_value = max(lower, min(upper, next_value))

        if isinstance(current_value, int) and not isinstance(current_value, bool):
            return int(round(next_value))
        if isinstance(value, int) and not isinstance(value, bool):
            return int(round(next_value))
        return next_value

    def _build_state_view(self, runtime_state: dict[str, Any]) -> dict[str, Any]:
        """Build normalized state views from raw runtime data."""
        user_state_raw = runtime_state.get("user_state", {})
        world_state_raw = runtime_state.get("world_state", {})
        normalized_user_state = self._normalize("user_state", user_state_raw)
        normalized_characters: dict[str, Any] = {}

        for character_id, character in (runtime_state.get("characters", {}) or {}).items():
            character_state_raw = character.get("state", {}) if isinstance(character, dict) else {}
            normalized_characters[character_id] = {
                "profile": character.get("profile", "") if isinstance(character, dict) else "",
                "memory": character.get("memory", "") if isinstance(character, dict) else "",
                "state": self._normalize("character_state", character_state_raw, character_id=character_id),
            }

        return {
            "config": runtime_state.get("config", {}),
            "goals": runtime_state.get("goals", {}),
            "world_state": self._normalize("world_state", world_state_raw),
            "user_state": normalized_user_state,
            "characters": normalized_characters,
        }

    def _player_display_name(self, player_profile: Any) -> str:
        """Extract a short display name from the player profile text."""
        if not isinstance(player_profile, str):
            return ""
        first_line = next((line.strip() for line in player_profile.splitlines() if line.strip()), "")
        if ":" in first_line:
            return first_line.split(":", 1)[1].strip()
        return first_line

    def _build_ui_goals_view(self, runtime_goals: Any) -> dict[str, Any]:
        """Return goal definitions plus runtime progress for frontend rendering."""
        if not isinstance(runtime_goals, dict):
            runtime_goals = {}
        base_goals = self.static_state.get("goals", {})
        return {
            "definitions": deepcopy(base_goals.get("goals", {})) if isinstance(base_goals, dict) else {},
            "endings": deepcopy(base_goals.get("endings", {})) if isinstance(base_goals, dict) else {},
            "active_goals": deepcopy(runtime_goals.get("active_goals", {})) if isinstance(runtime_goals.get("active_goals"), dict) else {},
            "available_goals": deepcopy(runtime_goals.get("available_goals", {})) if isinstance(runtime_goals.get("available_goals"), dict) else {},
            "completed_goals": list(runtime_goals.get("completed_goals", [])) if isinstance(runtime_goals.get("completed_goals"), list) else [],
            "ending_state": deepcopy(runtime_goals.get("ending_state", {})) if isinstance(runtime_goals.get("ending_state"), dict) else {},
        }

    def _build_agent_goals_view(self, runtime_goals: Any) -> dict[str, Any]:
        """Return only active goals with checkpoint runtime statuses for agent prompts."""
        if not isinstance(runtime_goals, dict):
            return {}

        goals = self._goal_definitions()
        active_goals = runtime_goals.get("active_goals", {})
        if not isinstance(goals, dict) or not isinstance(active_goals, dict):
            return {}

        active_goal_items: dict[str, Any] = {}

        for goal_id, runtime_checkpoints in active_goals.items():
            if goal_id not in goals:
                continue
            source_goal = goals[goal_id]
            if not isinstance(source_goal, dict):
                continue
            goal_item = {
                "title": source_goal.get("title", ""),
                "description": source_goal.get("description", ""),
            }
            if source_goal.get("type"):
                goal_item["type"] = source_goal.get("type")

            checkpoints = deepcopy(source_goal.get("checkpoints", []))
            if isinstance(checkpoints, list):
                for checkpoint in checkpoints:
                    if not isinstance(checkpoint, dict):
                        continue
                    checkpoint_state = runtime_checkpoints.get(checkpoint.get("id"), {}) if isinstance(runtime_checkpoints, dict) else {}
                    if isinstance(checkpoint_state, dict):
                        checkpoint.update(deepcopy(checkpoint_state))
                    checkpoint["status"] = self._normalize_checkpoint_status(checkpoint.get("status", "unstarted"))
                    checkpoint.setdefault("progress_note", "")
                goal_item["checkpoints"] = checkpoints
            else:
                goal_item["checkpoints"] = []
            active_goal_items[goal_id] = goal_item

        return {
            "active_goals": active_goal_items,
        }

    def _build_agent_stat_view(self, state_view: dict[str, Any]) -> dict[str, Any]:
        """Return player stat values plus semantic rules for agent prompts."""
        config = state_view.get("config", {})
        stat_rules = config.get("stat_rules", {}) if isinstance(config, dict) else {}
        user_state = state_view.get("user_state", {})
        return self._compact_stats(stat_rules, user_state.get("stats", {}))

    def _build_character_agent_stat_view(
        self,
        character_state: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Return character stat/relation values plus semantic rules."""
        stat_rules = config.get("stat_rules", {}) if isinstance(config, dict) else {}
        return self._compact_stats(stat_rules, character_state.get("stats", {}))

    def _expand_possessions(self, possessions: Any, config: Any) -> list[dict[str, Any]]:
        """Return possessed item ids enriched with config.items details."""
        if not isinstance(possessions, list):
            return []
        item_defs = config.get("items", {}) if isinstance(config, dict) else {}
        if not isinstance(item_defs, dict):
            item_defs = {}
        expanded: list[dict[str, Any]] = []
        for item_id in possessions:
            if not isinstance(item_id, str):
                continue
            item = item_defs.get(item_id, {})
            if isinstance(item, dict):
                expanded.append({"id": item_id, **item})
            else:
                expanded.append({"id": item_id})
        return expanded

    def _build_user_character_relations(self, characters: Any, config: Any) -> dict[str, Any]:
        """Summarize each character's relation to the player for agent prompts."""
        if not isinstance(characters, dict):
            return {}
        relation_rules = config.get("relation_rules", {}) if isinstance(config, dict) else {}
        player_rule = relation_rules.get("player", {}) if isinstance(relation_rules, dict) else {}
        relations: dict[str, Any] = {}
        for character_id, character in characters.items():
            character_state = character.get("state", {}) if isinstance(character, dict) else {}
            character_relations = character_state.get("relations", {}) if isinstance(character_state, dict) else {}
            if not isinstance(character_relations, dict):
                continue
            if "player" in character_relations:
                value = character_relations["player"]
                relations[character_id] = self._compact_one_rule(player_rule, value) if player_rule else value
        return relations

    def _compact_stats(self, rules: Any, stats: Any) -> dict[str, Any]:
        """Keep stat values, semantic rules, and currently active effects."""
        if not isinstance(rules, dict) or not isinstance(stats, dict):
            return {}
        compact: dict[str, Any] = {}
        for stat_name, stat_value in stats.items():
            rule = rules.get(stat_name, {})
            if not isinstance(rule, dict):
                continue
            current_value = stat_value.get("value") if isinstance(stat_value, dict) else stat_value
            compact[stat_name] = self._compact_one_rule(rule, current_value)
        return compact

    def _compact_one_rule(self, rule: dict[str, Any], value: Any) -> dict[str, Any]:
        """Return one LLM-facing stat item with its current value."""
        compact = {
            "value": value,
            "description": rule.get("description", ""),
            "update_guidance": rule.get("update_guidance", ""),
        }
        active_effects = self._active_effect_descriptions(rule, value)
        if active_effects:
            compact["active_effects"] = active_effects
        return compact

    def _without_display_names(self, value: Any) -> Any:
        """Remove UI-only display labels from LLM-facing state views."""
        if isinstance(value, dict):
            return {
                key: self._without_display_names(item)
                for key, item in value.items()
                if key != "display_name"
            }
        if isinstance(value, list):
            return [self._without_display_names(item) for item in value]
        return value

    def _active_effect_descriptions(self, rule: dict[str, Any], value: Any) -> list[str]:
        """Return descriptions for effects whose condition matches the current value."""
        effects = rule.get("effects", [])
        if not isinstance(effects, list):
            return []
        active: list[str] = []
        for effect in effects:
            if not isinstance(effect, dict):
                continue
            if self._condition_met(value, effect.get("condition", {})):
                description = effect.get("description")
                if description:
                    active.append(str(description))
        return active

    def _ensure_goal_runtime_fields(self) -> None:
        """Ensure runtime goals carry supported status fields."""
        runtime_state = self.get_runtime_state()
        goals_config = runtime_state.get("goals", {})
        if not isinstance(goals_config, dict):
            return
        if not isinstance(goals_config.get("active_goals"), dict):
            goals_config["active_goals"] = {}
        if not isinstance(goals_config.get("available_goals"), dict):
            goals_config["available_goals"] = {}
        if not isinstance(goals_config.get("completed_goals"), list):
            goals_config["completed_goals"] = []

    def _iter_goal_update_items(self, goal_update: dict[str, Any]) -> list[dict[str, Any]]:
        """Return normalized checkpoint update items from the canonical goal_update shape."""
        items: list[dict[str, Any]] = []
        for item in goal_update.get("checkpoints", []):
            if isinstance(item, dict):
                items.append(item)
        return items

    def _normalize_checkpoint_status(self, status: Any) -> str:
        """Normalize model-produced checkpoint statuses to the supported lifecycle."""
        normalized = str(status or "unstarted").strip().lower()
        return normalized if normalized in self.CHECKPOINT_STATUSES else "in_progress"

    def _can_advance_checkpoint(self, current_status: Any, next_status: str) -> bool:
        """Allow strictly forward checkpoint status transitions."""
        current = self._normalize_checkpoint_status(current_status or "unstarted")
        return self.CHECKPOINT_STATUSES.index(next_status) > self.CHECKPOINT_STATUSES.index(current)

    def _is_valid_active_checkpoint(
        self,
        goals: dict[str, Any],
        active_goal_ids: set[str],
        goal_id: str,
        checkpoint_id: str,
    ) -> bool:
        """Return whether a checkpoint belongs to an active known goal."""
        if not goal_id or not checkpoint_id or goal_id not in active_goal_ids:
            return False
        goal = goals.get(goal_id, {})
        checkpoints = goal.get("checkpoints", []) if isinstance(goal, dict) else []
        if not isinstance(checkpoints, list):
            return False
        return any(isinstance(item, dict) and item.get("id") == checkpoint_id for item in checkpoints)

    def _goal_checkpoints_complete(self, checkpoints: Any) -> bool:
        """Return whether every checkpoint of the goal has status=completed."""
        if not checkpoints:
            return False
        if isinstance(checkpoints, dict):
            return all(isinstance(item, dict) and item.get("status") == "completed" for item in checkpoints.values())
        if isinstance(checkpoints, list):
            return all(isinstance(item, dict) and item.get("status") == "completed" for item in checkpoints)
        return False

    def _refresh_goal_graph_state(
        self,
        goals: dict[str, Any],
        runtime_goals: dict[str, Any],
    ) -> dict[str, list[str]]:
        """Complete finished active goals and expose newly unlocked graph successors."""
        self._ensure_goal_runtime_fields()
        active_goals = runtime_goals["active_goals"]
        available_goals = runtime_goals["available_goals"]
        completed_goals = runtime_goals["completed_goals"]
        result: dict[str, list[str]] = {"completed_goals": [], "available_goals": []}

        for goal_id in list(active_goals):
            if goal_id in completed_goals or not self._goal_checkpoints_complete(active_goals.get(goal_id, {})):
                continue
            active_goals.pop(goal_id, None)
            completed_goals.append(goal_id)
            result["completed_goals"].append(goal_id)

        for goal_id in list(available_goals):
            if self._goal_blocked_by_selected(goal_id, goals, set(completed_goals)):
                available_goals.pop(goal_id, None)

        for goal_id in self._unlocked_goal_ids(goals, runtime_goals):
            if goal_id not in available_goals:
                available_goals[goal_id] = self._extract_goal_checkpoint_state(goals.get(goal_id, {}))
                result["available_goals"].append(goal_id)
        return result

    def _unlocked_goal_ids(self, goals: dict[str, Any], runtime_goals: dict[str, Any]) -> list[str]:
        """Return goals whose unlock_condition is satisfied."""
        completed = set(runtime_goals.get("completed_goals", []))
        active = set(runtime_goals.get("active_goals", {}))
        available = set(runtime_goals.get("available_goals", {}))
        unlocked: list[str] = []
        for goal_id in goals:
            if goal_id in completed or goal_id in active or goal_id in available:
                continue
            if self._goal_unlock_condition_met(goal_id, goals, runtime_goals):
                unlocked.append(goal_id)
        return unlocked

    def _goal_unlock_condition_met(
        self,
        goal_id: str,
        goals: dict[str, Any],
        runtime_goals: dict[str, Any],
    ) -> bool:
        """Return whether a goal can enter available_goals."""
        completed = set(runtime_goals.get("completed_goals", []))
        goal = goals.get(goal_id, {})
        condition = goal.get("unlock_condition", {}) if isinstance(goal, dict) else {}
        predecessors = self._predecessor_goal_ids(goals, goal_id)

        if self._goal_blocked_by_selected(goal_id, goals, completed):
            return False
        if not isinstance(condition, dict) or not condition:
            return any(predecessor in completed for predecessor in predecessors)

        all_of = condition.get("all_of")
        if isinstance(all_of, list) and not set(all_of).issubset(completed):
            return False

        n_of = condition.get("n_of")
        if isinstance(n_of, dict):
            items = n_of.get("items", predecessors)
            if not isinstance(items, list):
                return False
            try:
                minimum = max(1, int(n_of.get("min", 1)))
            except (TypeError, ValueError):
                minimum = 1
            if sum(1 for item in items if item in completed) < minimum:
                return False

        if "all_of" not in condition and "n_of" not in condition:
            return any(predecessor in completed for predecessor in predecessors)
        return True

    def _predecessor_goal_ids(self, goals: dict[str, Any], goal_id: str) -> list[str]:
        """Return goals with next edges pointing to goal_id."""
        predecessors: list[str] = []
        for candidate_id, goal in goals.items():
            next_ids = goal.get("next", []) if isinstance(goal, dict) else []
            if isinstance(next_ids, list) and goal_id in next_ids:
                predecessors.append(candidate_id)
        return predecessors

    def _goal_blocked_by_selected(self, goal_id: str, goals: dict[str, Any], selected_goal_ids: set[str]) -> bool:
        """Return whether selected goals block this goal's route."""
        if not selected_goal_ids:
            return False
        if selected_goal_ids.intersection(self._blocked_by_goal_ids(goals.get(goal_id, {}))):
            return True
        return any(goal_id in self._blocked_by_goal_ids(goals.get(selected_id, {})) for selected_id in selected_goal_ids)

    def _blocked_by_goal_ids(self, goal: Any) -> set[str]:
        """Return goal ids that are mutually exclusive with this goal."""
        if not isinstance(goal, dict):
            return set()
        condition = goal.get("unlock_condition", {})
        blocked_by = condition.get("blocked_by", []) if isinstance(condition, dict) else []
        return {str(goal_id) for goal_id in blocked_by if goal_id}

    def _move_goal_between_lists(self, goal_id: str, source: str, target: str) -> dict[str, Any]:
        """Move a goal between active and available lists without touching progress."""
        goal_id = str(goal_id or "").strip()
        runtime_state = self.get_runtime_state()
        goals_config = runtime_state.setdefault("goals", {})
        goals = self._goal_definitions()
        if not goal_id or goal_id not in goals or not isinstance(goals_config, dict):
            raise ValueError(f"Unknown goal: {goal_id}")

        self._ensure_goal_runtime_fields()
        if goal_id in goals_config["completed_goals"]:
            raise ValueError(f"Completed goal cannot be moved: {goal_id}")
        if goal_id not in goals_config[source]:
            raise ValueError(f"Goal is not in {source}: {goal_id}")
        if target == "active_goals" and self._goal_blocked_by_selected(
            goal_id,
            goals,
            set(goals_config["active_goals"]) | set(goals_config["completed_goals"]),
        ):
            raise ValueError(f"Goal is blocked by another active or completed goal: {goal_id}")

        goal_progress = goals_config[source].pop(goal_id)
        goals_config[target][goal_id] = goal_progress
        self._write_runtime_state()
        return {
            "goal_id": goal_id,
            "active_goals": list(goals_config["active_goals"]),
            "available_goals": list(goals_config["available_goals"]),
        }

    def _ending_required_met(self, runtime_state: dict[str, Any], required: Any) -> bool:
        """Return whether all configured ending requirements are satisfied."""
        if not isinstance(required, dict):
            return False
        completed_goals = set(runtime_state.get("goals", {}).get("completed_goals", []))
        for key, condition in required.items():
            if key == "goals":
                if not isinstance(condition, list) or not set(condition).issubset(completed_goals):
                    return False
                continue
            if not self._condition_met(self._read_state_path(runtime_state, key), condition):
                return False
        return True

    def _read_state_path(self, runtime_state: dict[str, Any], dotted_path: str) -> Any:
        """Read a dotted state path, using character.state as the character payload."""
        parts = [part for part in dotted_path.split(".") if part]
        cursor: Any = runtime_state
        index = 0
        while index < len(parts):
            part = parts[index]
            if isinstance(cursor, dict) and part in cursor:
                cursor = cursor[part]
                if part == "characters" and index + 1 < len(parts):
                    character_id = parts[index + 1]
                    character = cursor.get(character_id, {}) if isinstance(cursor, dict) else {}
                    cursor = character.get("state", {}) if isinstance(character, dict) else {}
                    index += 2
                    continue
                index += 1
                continue
            return None
        return cursor

    def _condition_met(self, value: Any, condition: Any) -> bool:
        """Evaluate one simple condition with eq/ne/gte/lte/gt/lt operators."""
        if isinstance(condition, str) and "-" in condition:
            try:
                lower_text, upper_text = condition.split("-", 1)
                left = float(value)
                return float(lower_text) <= left <= float(upper_text)
            except (TypeError, ValueError):
                return False
        if not isinstance(condition, dict):
            return value == condition
        for operator, expected in condition.items():
            if operator == "eq" and value != expected:
                return False
            if operator == "ne" and value == expected:
                return False
            if operator in {"gte", "lte", "gt", "lt"}:
                try:
                    left = float(value)
                    right = float(expected)
                except (TypeError, ValueError):
                    return False
                if operator == "gte" and left < right:
                    return False
                if operator == "lte" and left > right:
                    return False
                if operator == "gt" and left <= right:
                    return False
                if operator == "lt" and left >= right:
                    return False
        return True

    def _normalize(self, kind: str, payload: Any, character_id: str | None = None) -> dict[str, Any]:
        """Normalize loose model output into the structure defined by base templates."""
        if not isinstance(payload, dict):
            return {}

        template = self._get_base_template(kind, character_id=character_id)
        if not template:
            return {}

        normalized = dict(payload)
        if kind == "character_state":
            normalized = self._normalize_relation_aliases(normalized)

        if kind in {"user_state", "character_state"}:
            normalized = self._normalize_state_groups(normalized, template)

        return self._project_to_template(normalized, template)

    def _normalize_relation_aliases(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize relation/relation.user aliases into relations.player."""
        normalized = dict(payload)
        merged_relations: dict[str, Any] = {}
        for key in ("relation", "relations"):
            value = normalized.pop(key, None)
            if isinstance(value, dict):
                merged_relations.update(value)
        if "user" in merged_relations and "player" not in merged_relations:
            merged_relations["player"] = merged_relations.pop("user")
        if not merged_relations:
            return normalized

        existing_relations = normalized.get("relations", {})
        if not isinstance(existing_relations, dict):
            existing_relations = {}
        normalized["relations"] = {**existing_relations, **merged_relations}
        return normalized

    def _normalize_state_groups(self, payload: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
        """Move loose numeric fields into stats/relations according to the template."""
        normalized = dict(payload)
        for group_name in ("stats", "relations"):
            group_template = template.get(group_name, {})
            if not isinstance(group_template, dict):
                continue
            nested_group = normalized.pop(group_name, {})
            if not isinstance(nested_group, dict):
                nested_group = {}
            normalized_group: dict[str, Any] = {}
            for field_name in group_template:
                value = nested_group.get(field_name)
                if value is None and field_name in normalized:
                    value = normalized.pop(field_name)
                normalized_value = self._normalize_value_entry(value)
                if normalized_value:
                    normalized_group[field_name] = normalized_value
            if normalized_group:
                normalized[group_name] = normalized_group
        return normalized

    def _normalize_value_entry(self, value: Any) -> dict[str, Any]:
        """Normalize one stat/relation entry into a {value, reason?} dict."""
        value = self._coerce_delta_value(value)
        if isinstance(value, dict):
            cleaned = {
                nested_key: nested_value
                for nested_key, nested_value in value.items()
                if nested_key in {"value", "description", "reason"}
            }
            return cleaned
        if isinstance(value, (int, float)):
            return {"value": value}
        return {}

    def _project_to_template(self, payload: Any, template: Any) -> Any:
        """Project data onto the structure defined by a base template."""
        if isinstance(template, dict):
            if "value" in template and isinstance(payload, dict):
                projected = {}
                if "value" in payload:
                    projected["value"] = self._coerce_delta_value(payload.get("value"))
                if "description" in payload and "description" in template:
                    projected["description"] = payload.get("description")
                if "reason" in payload:
                    projected["reason"] = payload.get("reason")
                return projected
            if not isinstance(payload, dict):
                if "value" in template and isinstance(payload, (int, float, str, bool)):
                    projected = {"value": self._coerce_delta_value(payload)}
                    if "description" in template:
                        projected["description"] = template.get("description")
                    return projected
                return {}
            projected: dict[str, Any] = {}
            for key, template_value in template.items():
                if key not in payload:
                    continue
                value = self._project_to_template(payload[key], template_value)
                if isinstance(value, dict) and not value:
                    continue
                projected[key] = value
            return projected
        return self._coerce_delta_value(payload)

    def _coerce_delta_value(self, value: Any) -> Any:
        """Convert model display deltas like 'old -> new' into the intended new value."""
        if not isinstance(value, str):
            return value
        separator = "→" if "→" in value else "->" if "->" in value else None
        if not separator:
            return value
        next_text = value.split(separator)[-1].strip()
        if not next_text:
            return value
        try:
            number = float(next_text)
        except ValueError:
            return next_text
        return int(number) if number.is_integer() else number
