from __future__ import annotations

import json
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .domains import CharactersDomain, LocationsDomain, MemoryDomain, PossessionsDomain, RelationsDomain, StatsDomain, StoryDomain
from .domains.base import DomainContext


class StateManager:
    """Owns all runtime state reads and writes."""

    RECENT_TURN_MEMORY_LIMIT = 5
    STATIC_WORLD_KEYS = {"world_id", "map_locations", "organizations", "description", "summary"}
    STATIC_USER_KEYS = {"name"}

    def __init__(self, game_root: Path, user_game_root: Path | None = None) -> None:
        self.game_root = game_root
        self.base_dir = game_root / "base"
        self.user_game_root = user_game_root or game_root
        self.runtime_dir = self.user_game_root / "runtime"
        self.saves_dir = self.user_game_root / "saves"
        self.undo_dir = self.user_game_root / "undo" / "latest_before_turn"
        self.runtime_state: dict[str, Any] | None = None
        self.logs: dict[str, list[dict[str, Any]]] = {"turn_log": []}
        self.static_state: dict[str, Any] = self._load_static_state()
        self._base_template_cache: dict[str, Any] = {
            "player": self.static_state.get("player", {}),
            "world": self.static_state.get("world", {}),
            "character_state": self.static_state.get("character_states", {}),
        }
        self.stats_domain = StatsDomain()
        self.relations_domain = RelationsDomain()
        self.characters_domain = CharactersDomain()
        self.possessions_domain = PossessionsDomain()
        self.locations_domain = LocationsDomain()
        self.memory_domain = MemoryDomain(self.RECENT_TURN_MEMORY_LIMIT)
        self.story_domain = StoryDomain()
        self.domains = [
            self.characters_domain,
            self.stats_domain,
            self.relations_domain,
            self.possessions_domain,
            self.locations_domain,
            self.memory_domain,
            self.story_domain,
        ]
        self.agent_view_domains = [
            self.story_domain,
            self.locations_domain,
            self.stats_domain,
            self.relations_domain,
            self.possessions_domain,
            self.memory_domain,
        ]

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
        context = self._domain_context(state_view["config"])
        player = state_view.get("player", {})
        if isinstance(player, dict):
            if runtime_state.get("player_profile"):
                player["player_profile"] = runtime_state["player_profile"]
        state_view["player_display_name"] = self._player_display_name(runtime_state.get("player_profile", ""))
        for domain in self.agent_view_domains:
            state_view = domain.get_agent_view(state_view, context)
        return state_view

    def get_ui_state_view(self) -> dict[str, Any]:
        """Return a normalized state view for frontend rendering."""
        runtime_state = self.get_runtime_state()
        state_view = self._build_state_view(runtime_state)
        state_view["story"] = self.story_domain.get_ui_view(
            runtime_state,
            self._domain_context(state_view.get("config", {})),
        )
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

    def apply_update(self, turn_record: dict[str, Any]) -> dict[str, Any]:
        """Apply all runtime changes described by one unified turn record."""
        runtime_state = self.get_runtime_state()
        context = self._domain_context()
        state_changes: dict[str, Any] = {"world": {}, "player": {}, "characters": {}}

        for domain in self.domains:
            changes = domain.apply_update(runtime_state, turn_record, context)
            if not isinstance(changes, dict):
                continue
            for key, value in changes.items():
                if key == "characters" and isinstance(value, dict):
                    for character_id, character_changes in value.items():
                        if character_changes:
                            state_changes["characters"].setdefault(character_id, {}).update(character_changes)
                elif isinstance(value, dict) and value:
                    state_changes.setdefault(key, {}).update(value)

        turn_record["state_changes"] = state_changes
        self._write_runtime_state()
        resolve = turn_record.get("director_resolve", {})
        return {
            "state_changes": state_changes,
            "story_update": resolve.get("story_update", {}) if isinstance(resolve, dict) else {},
            "ending": resolve.get("ending", {}) if isinstance(resolve, dict) else {},
        }

    def update_ending_narrative(self, narrative: str) -> dict[str, Any]:
        """Persist the generated ending narrative into story.ending_state."""
        runtime_state = self.get_runtime_state()
        story_state = runtime_state.setdefault("story", {})
        if not isinstance(story_state, dict):
            return {}
        ending_state = story_state.get("ending_state", {})
        if not isinstance(ending_state, dict) or not ending_state.get("is_ended"):
            return {}
        cleaned = str(narrative or "").strip()
        if not cleaned:
            return ending_state
        ending_state["narrative"] = cleaned
        self._write_runtime_state()
        return ending_state

    def apply_player_customization(self, values: dict[str, Any]) -> dict[str, Any]:
        """Persist opening player customization as a text profile."""
        if not isinstance(values, dict):
            return {}
        runtime_state = self.get_runtime_state()
        config = runtime_state.get("config", {})
        customization = config.get("player_customization", {}) if isinstance(config, dict) else {}
        if not isinstance(customization, dict) or not customization:
            return {}
        profile = customization.get("profile")
        uses_profile_template = isinstance(customization.get("fields"), dict) or (
            isinstance(profile, dict) and isinstance(profile.get("template"), str)
        )
        field_configs = customization.get("fields") if uses_profile_template else customization
        if not isinstance(field_configs, dict):
            field_configs = {}
        profile_config = profile if uses_profile_template and isinstance(profile, dict) else None

        accepted: dict[str, Any] = {}
        for field_name, field_config in field_configs.items():
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
        if profile_config is not None and isinstance(values.get("profile"), str):
            accepted["profile"] = values["profile"].strip()

        if accepted:
            profile_text = self._build_player_profile_text(accepted, field_configs, profile_config)
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
        slot_dir = self._slot_path(slot_id)
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
        slot_dir = self._slot_path(slot_id) / "runtime"
        if not slot_dir.exists():
            raise FileNotFoundError(f"Save slot not found: {slot_id}")
        if self.runtime_dir.exists():
            shutil.rmtree(self.runtime_dir)
        shutil.copytree(slot_dir, self.runtime_dir)
        self.clear_latest_turn_snapshot()
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

    def delete_save(self, slot_id: str) -> None:
        """Delete one save slot."""
        slot_dir = self._slot_path(slot_id)
        if not slot_dir.exists():
            raise FileNotFoundError(f"Save slot not found: {slot_id}")
        shutil.rmtree(slot_dir)

    def rename_save(self, old_slot_id: str, new_slot_id: str) -> None:
        """Rename one save slot without changing its runtime contents."""
        old_slot_dir = self._slot_path(old_slot_id)
        new_slot_dir = self._slot_path(new_slot_id)
        if not old_slot_dir.exists():
            raise FileNotFoundError(f"Save slot not found: {old_slot_id}")
        if new_slot_dir.exists():
            raise FileExistsError(f"Save slot already exists: {new_slot_id}")
        old_slot_dir.rename(new_slot_dir)

    def create_latest_turn_snapshot(self) -> None:
        """Save runtime before a turn so the latest input can be reverted."""
        if self.undo_dir.exists():
            shutil.rmtree(self.undo_dir)
        if self.runtime_dir.exists():
            shutil.copytree(self.runtime_dir, self.undo_dir)

    def revert_latest_turn(self) -> None:
        """Restore runtime to the snapshot taken before the latest turn."""
        if not self.undo_dir.exists():
            raise FileNotFoundError("No latest turn snapshot found.")
        if self.runtime_dir.exists():
            shutil.rmtree(self.runtime_dir)
        shutil.copytree(self.undo_dir, self.runtime_dir)
        self.clear_latest_turn_snapshot()
        self.initialize_runtime()

    def clear_latest_turn_snapshot(self) -> None:
        """Remove the latest-turn undo snapshot."""
        if self.undo_dir.exists():
            shutil.rmtree(self.undo_dir)

    def _domain_context(self, config: dict[str, Any] | None = None) -> DomainContext:
        """Build shared helpers for domain view/update classes."""
        runtime_config = config if config is not None else self.get_runtime_state().get("config", {})
        return DomainContext(
            config=runtime_config if isinstance(runtime_config, dict) else {},
            static_state=self.static_state,
            runtime_dir=self.runtime_dir,
            turn_number=self.get_turn_count() + 1,
            condition_met=self._condition_met,
            coerce_value=self._coerce_delta_value,
            record_change=self._record_change,
        )

    def _load_logs(self) -> dict[str, list[dict[str, Any]]]:
        """Load runtime JSONL turn logs into memory."""
        logs_dir = self.runtime_dir / "logs"
        return {
            "turn_log": self._read_jsonl(logs_dir / "turn_log.jsonl"),
        }

    def _append_log_record(self, log_name: str, record: dict[str, Any]) -> None:
        """Append one log record to the in-memory cache and JSONL file."""
        payload = dict(record)
        payload.setdefault("timestamp", datetime.utcnow().isoformat(timespec="seconds") + "Z")
        self.logs.setdefault(log_name, []).append(payload)
        self._append_jsonl(self.runtime_dir / "logs" / f"{log_name}.jsonl", payload)

    def _slot_path(self, slot_id: str) -> Path:
        """Resolve a save slot path while rejecting path traversal."""
        clean_slot_id = str(slot_id or "").strip()
        if not clean_slot_id or len(Path(clean_slot_id).parts) != 1 or clean_slot_id in {".", ".."}:
            raise ValueError("Invalid save slot id.")
        slot_dir = (self.saves_dir / clean_slot_id).resolve()
        saves_root = self.saves_dir.resolve()
        if saves_root != slot_dir and saves_root not in slot_dir.parents:
            raise ValueError("Invalid save slot id.")
        return slot_dir

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
            "player": self._read_json(self.base_dir / "user_state.json", default={}),
            "world": self._read_json(self.base_dir / "world_state.json", default={}),
            "character_states": character_states,
            "character_profiles": character_profiles,
            "story": self._read_json(self.base_dir / "story.json", default={}),
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
            "story": deepcopy(state_data.get("story") or self._initial_runtime_story()),
            "world": self._compose_world_state(
                self.static_state.get("world", {}),
                state_data.get("world", {}),
            ),
            "player": self._extract_runtime_player(state_data.get("player") or self.static_state.get("player", {})),
            "characters": characters,
            "player_profile": self._read_text(self.runtime_dir / "player_profile.txt"),
        }

    def _build_player_profile_text(
        self,
        accepted: dict[str, Any],
        allowed_fields: dict[str, Any],
        profile_config: dict[str, Any] | None = None,
    ) -> str:
        """Format player customization values for agent prompts."""
        profile_text = str(accepted.get("profile", "")).strip()
        if profile_config is not None:
            lines = []
            if "name" in accepted:
                name_config = allowed_fields.get("name", {})
                label = name_config.get("label", "姓名") if isinstance(name_config, dict) else "姓名"
                lines.append(f"{label}:{accepted['name']}")
            if profile_text:
                if lines:
                    lines.append("")
                lines.append(f"{profile_config.get('label', '玩家设定')}:")
                lines.append(profile_text)
            return "\n".join(lines)

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
            "story": self._initial_runtime_story(),
            "world": self._extract_runtime_world(self.static_state.get("world", {})),
            "player": self._extract_runtime_player(self.static_state.get("player", {})),
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
                "story": deepcopy(runtime_state.get("story", {})),
                "world": self._extract_runtime_world(runtime_state.get("world", {})),
                "player": self._extract_runtime_player(runtime_state.get("player", {})),
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

    def _extract_runtime_world(self, world_state: Any) -> dict[str, Any]:
        """Keep only dynamic world fields in runtime/state.json."""
        if not isinstance(world_state, dict):
            return {}
        runtime_world = {
            key: deepcopy(value)
            for key, value in world_state.items()
            if key not in self.STATIC_WORLD_KEYS
        }
        runtime_world.setdefault("total_minutes", 0)
        runtime_world.setdefault("total_turns", 0)
        return runtime_world

    def _extract_runtime_player(self, user_state: Any) -> dict[str, Any]:
        """Keep only runtime user-state fields."""
        if not isinstance(user_state, dict):
            return {}
        return {
            key: deepcopy(value)
            for key, value in user_state.items()
            if key not in self.STATIC_USER_KEYS
        }

    def _initial_runtime_story(self) -> dict[str, Any]:
        """Build initial runtime story progress from base/story.json."""
        return self.story_domain.initial_runtime_story(self.static_state.get("story", {}))

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

    def _build_state_view(self, runtime_state: dict[str, Any]) -> dict[str, Any]:
        """Build normalized state views from raw runtime data."""
        player_raw = runtime_state.get("player", {})
        world_raw = runtime_state.get("world", {})
        normalized_player = self._normalize("player", player_raw)
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
            "story": runtime_state.get("story", {}),
            "world": self._normalize("world", world_raw),
            "player": normalized_player,
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

        if kind in {"player", "character_state"}:
            normalized = self._normalize_state_groups(normalized, template)

        return self._project_to_template(normalized, template)

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
            if group_name == "relations":
                projected_relations = self._project_to_template(nested_group, group_template)
                if projected_relations:
                    normalized[group_name] = projected_relations
                continue
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
