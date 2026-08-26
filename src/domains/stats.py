from __future__ import annotations

from typing import Any

from .base import DomainContext


class StatsDomain:
    name = "stats"
    update_tag = "state_update"
    update_key = "stats"

    def get_agent_view(self, state_view: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        player = state_view.get("player", {})
        player["stats"] = self._compact(player.get("stats", {}), context.config.get("stat_rules", {}), context)
        for character in state_view.get("characters", {}).values():
            character_state = character.get("state", {})
            character_state["stats"] = self._compact(
                character_state.get("stats", {}),
                context.config.get("stat_rules", {}),
                context,
            )
        return state_view

    def apply_update(self, runtime_state: dict[str, Any], turn_record: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        changes: dict[str, Any] = {"world": {}, "player": {}, "characters": {}}
        state_update = turn_record.get("director_resolve", {}).get("state_update", {})
        if isinstance(state_update, dict):
            changes["world"] = self._apply_state_stats(
                runtime_state.setdefault("world", {}),
                state_update.get("world", {}),
                context,
                ("stats",),
            )
            changes["player"] = self._apply_state_stats(
                runtime_state.setdefault("player", {}),
                state_update.get("player", {}),
                context,
                ("stats",),
            )

        runtime_characters = runtime_state.setdefault("characters", {})
        for character_id, character_record in (turn_record.get("characters") or {}).items():
            reflection = character_record.get("reflection", {}) if isinstance(character_record, dict) else {}
            update = reflection.get("state_update", {}) if isinstance(reflection, dict) else {}
            runtime_character = runtime_characters.get(character_id, {})
            if isinstance(runtime_character, dict):
                state = runtime_character.setdefault("state", {})
                character_changes = self._apply_state_stats(state, update, context, ("stats",))
                if character_changes:
                    changes["characters"].setdefault(character_id, {}).update(character_changes)
        return {key: value for key, value in changes.items() if value}

    def _apply_state_stats(
        self,
        target_state: dict[str, Any],
        update: Any,
        context: DomainContext,
        path: tuple[str, ...],
    ) -> dict[str, Any]:
        if not isinstance(target_state, dict) or not isinstance(update, dict):
            return {}
        stats = target_state.get("stats", {})
        if not isinstance(stats, dict):
            return {}
        update = update.get("stats", update)
        if not isinstance(update, dict):
            return {}
        return self._apply_metric_group(stats, update, context.config.get("stat_rules", {}), context, path)

    def _compact(self, values: Any, rules: Any, context: DomainContext) -> dict[str, Any]:
        if not isinstance(values, dict) or not isinstance(rules, dict):
            return {}
        compact: dict[str, Any] = {}
        for name, entry in values.items():
            rule = rules.get(name, {})
            if not isinstance(rule, dict):
                continue
            current_value = entry.get("value") if isinstance(entry, dict) else entry
            item = {
                "value": current_value,
                "description": rule.get("description", ""),
                "update_guidance": rule.get("update_guidance", ""),
            }
            active_effects = self._active_effects(rule, current_value, context)
            if active_effects:
                item["active_effects"] = active_effects
            compact[name] = item
        return compact

    def _apply_metric_group(
        self,
        target_group: Any,
        update: Any,
        rules: Any,
        context: DomainContext,
        path: tuple[str, ...],
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if not isinstance(target_group, dict) or not isinstance(update, dict):
            return changes
        rules = rules if isinstance(rules, dict) else {}
        for name, delta in update.items():
            current_entry = target_group.get(name)
            if not isinstance(current_entry, dict) or "value" not in current_entry:
                continue
            old_value = current_entry.get("value")
            reason = str(delta.get("reason", "") or "").strip() if isinstance(delta, dict) else ""
            next_value = delta.get("value") if isinstance(delta, dict) and "value" in delta else delta
            current_entry["value"] = self._clamp(next_value, old_value, rules.get(name, {}), context)
            if isinstance(delta, dict) and "description" in delta and "description" in current_entry:
                current_entry["description"] = delta["description"]
            context.record_change(changes, path + (name, "value"), old_value, current_entry.get("value"), reason)
        return changes

    def _active_effects(self, rule: dict[str, Any], value: Any, context: DomainContext) -> list[str]:
        effects = rule.get("effects", [])
        if not isinstance(effects, list):
            return []
        return [
            str(effect.get("description", "")).strip()
            for effect in effects
            if isinstance(effect, dict)
            and effect.get("description")
            and context.condition_met(value, effect.get("condition", {}))
        ]

    def _clamp(self, value: Any, current_value: Any, rule: Any, context: DomainContext) -> Any:
        value = context.coerce_value(value)
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
            next_value = max(current_number - max_delta, min(current_number + max_delta, next_value))

        if isinstance(current_value, int) and not isinstance(current_value, bool):
            return int(round(next_value))
        if isinstance(value, int) and not isinstance(value, bool):
            return int(round(next_value))
        return next_value
