from __future__ import annotations

from typing import Any

from .base import DomainContext
from .stats import StatsDomain


class RelationsDomain:
    name = "relations"
    update_tag = "state_update"
    update_key = "relations"

    def __init__(self) -> None:
        self.stats = StatsDomain()

    def get_agent_view(self, state_view: dict[str, Any], context: DomainContext) -> dict[str, Any]:
        characters = state_view.get("characters", {})
        state_view["user_state"]["relations"] = self.get_player_agent_view(characters, context)
        for character in characters.values():
            character_state = character.get("state", {})
            character_state["relations"] = self._compact_relations(character_state.get("relations", {}), context)
        return state_view

    def _compact_relations(self, value: Any, context: DomainContext) -> dict[str, Any]:
        rules = context.config.get("relation_rules", {})
        if not isinstance(value, dict):
            return {}
        compact: dict[str, Any] = {}
        for target_id, metrics in value.items():
            target_view = self.stats._compact(metrics, rules, context)
            if target_view:
                compact[target_id] = target_view
        return compact

    def get_player_agent_view(self, characters: Any, context: DomainContext) -> dict[str, Any]:
        if not isinstance(characters, dict):
            return {}
        relations: dict[str, Any] = {}
        for character_id, character in characters.items():
            state = character.get("state", {}) if isinstance(character, dict) else {}
            relation_to_player = state.get("relations", {}).get("player", {}) if isinstance(state, dict) else {}
            view = self.stats._compact(relation_to_player, context.config.get("relation_rules", {}), context)
            if view:
                relations[character_id] = view
        return relations

    def apply_update(
        self,
        target_state: dict[str, Any],
        update: Any,
        context: DomainContext,
        path: tuple[str, ...] = ("relations",),
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if not isinstance(update, dict):
            return changes
        relations = target_state.setdefault("relations", {})
        if not isinstance(relations, dict):
            return changes
        rules = context.config.get("relation_rules", {})
        for target_id, target_delta in update.items():
            target_group = relations.get(target_id)
            if not isinstance(target_group, dict):
                continue
            changes.update(
                self.stats._apply_metric_group(
                    target_group,
                    target_delta,
                    rules,
                    context,
                    path + (str(target_id),),
                )
            )
        return changes
