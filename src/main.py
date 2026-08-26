from __future__ import annotations

import json
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator

from .character_agent import CharacterAgent
from .director_agent import DirectorAgent
from .environment_layer import EnvironmentLayer
from .llm_client import AgentLLMConfig, OpenAIClient, read_llm_config
from .schemas import INPUT_MODE_HYBRID
from .state_manager import StateManager
from .user_layer import UserLayer


@dataclass
class AppConfig:
    game_id: str
    games_dir: Path
    users_dir: Path
    host: str
    port: int
    user_id: str
    director: AgentLLMConfig
    character: AgentLLMConfig
    access: dict[str, Any]
    debug: bool

    def with_game_id(self, game_id: str) -> "AppConfig":
        return replace(self, game_id=game_id)

    def with_user_id(self, user_id: str) -> "AppConfig":
        return replace(self, user_id=user_id)

    def with_llm_enabled(self, enabled: bool) -> "AppConfig":
        return replace(
            self,
            director=replace(self.director, enabled=enabled),
            character=replace(self.character, enabled=enabled),
        )


def load_app_config(config_path: str | Path | None = None) -> AppConfig:
    project_root = Path(__file__).resolve().parent.parent
    path = Path(config_path) if config_path else project_root / "config.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing config file: {path}. Copy config.example.json to config.json and fill in your local settings."
        )

    config = json.loads(path.read_text(encoding="utf-8"))
    game_config = config.get("game", {}) if isinstance(config.get("game"), dict) else {}
    server_config = config.get("server", {}) if isinstance(config.get("server"), dict) else {}
    return AppConfig(
        game_id=str(game_config.get("default_game_id") or "demo_game"),
        games_dir=_resolve_path(project_root, game_config.get("games_dir") or "games"),
        users_dir=_resolve_path(project_root, game_config.get("users_dir") or "users"),
        host=str(server_config.get("host") or "127.0.0.1"),
        port=int(server_config.get("port") or 8000),
        user_id="root",
        director=read_llm_config(_agent_llm_block(config, "director")),
        character=read_llm_config(_agent_llm_block(config, "character")),
        access=config.get("access", {}) if isinstance(config.get("access"), dict) else {},
        debug=bool(config.get("debug", False)),
    )


def _agent_llm_block(config: dict[str, Any], agent_key: str) -> dict[str, Any]:
    agent_config = config.get(agent_key, {})
    if not isinstance(agent_config, dict):
        return {}
    llm_config = agent_config.get("llm", {})
    return llm_config if isinstance(llm_config, dict) else {}


def _resolve_path(project_root: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else project_root / path


class GameApp:
    """Orchestrates the four-layer game loop."""

    def __init__(
        self,
        game_id: str,
        user_layer: UserLayer,
        director: DirectorAgent,
        environment: EnvironmentLayer,
        state_manager: StateManager,
    ) -> None:
        self.game_id = game_id
        self.user_layer = user_layer
        self.director = director
        self.environment = environment
        self.state_manager = state_manager
        self.next_input_mode = INPUT_MODE_HYBRID
        self.next_choices: list[dict[str, Any]] = []
        self._bootstrapped = False

    def bootstrap_runtime(self) -> None:
        if self._bootstrapped:
            return
        self.state_manager.initialize_runtime()
        self._bootstrapped = True
        self._restore_ui_progress_from_logs()

    def get_current_state(self) -> dict[str, Any]:
        self.bootstrap_runtime()
        return self.state_manager.get_ui_state_view()

    def get_current_logs(self, limit: int | None = 50) -> dict[str, Any]:
        self.bootstrap_runtime()
        return self.state_manager.get_logs(limit=limit)

    def get_opening_payload(self) -> dict[str, Any]:
        state = self.get_current_state()
        config = state.get("config", {})
        opening_text = config.get("opening") or "游戏开始。输入 `quit` 可退出。"
        return {
            "narrative": opening_text,
            "story_update": {},
            "interaction": {"mode": self.next_input_mode, "options": []},
            "state_update": {},
        }

    def get_ui_state(self, log_limit: int | None = 50) -> dict[str, Any]:
        state = self.get_current_state()
        logs = self.get_current_logs(limit=log_limit)
        turns = logs.get("turn_log", [])
        latest_turn = turns[-1] if turns else self.get_opening_payload()
        return {
            "game_id": self.game_id,
            "state": state,
            "ui": {
                "turns": turns,
                "messages": logs.get("turn_log", []),
                "latest_turn": latest_turn,
                "interaction": {"mode": self.next_input_mode, "options": self.next_choices},
                "input_mode": self.next_input_mode,
                "choices": self.next_choices,
                "saves": self.state_manager.list_saves(),
            },
        }

    def process_turn(self, user_input: dict[str, Any]) -> dict[str, Any]:
        self.bootstrap_runtime()
        self.state_manager.create_latest_turn_snapshot()
        state_before = self.state_manager.get_agent_state_view()
        logs = self.state_manager.get_logs()
        turn_index = self.state_manager.get_turn_count() + 1
        agent_user_input = str(user_input.get("raw_text") or user_input.get("selected_choice") or "").strip()
        turn_record = {
            "turn_index": turn_index,
            "user_input": user_input,
            "director_plan": {},
            "dialogues": [],
            "characters": {},
            "director_narrative": {},
            "director_resolve": {},
            "state_changes": {},
        }

        plan = self.director.plan(user_input=agent_user_input, state=state_before, logs=logs).get("director_plan", {})
        turn_record["director_plan"] = plan
        player_input = plan.get("player_input", {})
        ordered_feedback = self._run_dialogue(plan, state_before)
        turn_record["dialogues"] = ordered_feedback

        narrative_env_feedback = self._narrative_env_feedback(ordered_feedback)
        env_feedback = self.environment.finalize_feedback(ordered_feedback)
        narrative_result = self.director.narrative(
            env_feedback=narrative_env_feedback,
            state=state_before,
            logs=logs,
            user_input=player_input,
            story_guidance=plan.get("story_guidance", ""),
        ).get("director_narrative", {})
        turn_record["director_narrative"] = narrative_result

        resolve_part, reflection_parts = self._resolve_and_reflect(
            ordered_feedback,
            narrative_result,
            state_before,
            logs,
            player_input,
        )
        turn_record["director_resolve"] = resolve_part.get("director_resolve", {})
        for character_id, reflection_part in reflection_parts.items():
            character = (reflection_part.get("characters") or {}).get(character_id, {})
            if isinstance(character, dict):
                turn_record["characters"].setdefault(character_id, {}).update(character)
        self._append_event_memories(turn_record, state_before)
        return self._finalize_turn(turn_record, env_feedback, state_before)

    def stream_turn(self, user_input: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self.bootstrap_runtime()
        self.state_manager.create_latest_turn_snapshot()
        state_before = self.state_manager.get_agent_state_view()
        logs = self.state_manager.get_logs()
        turn_index = self.state_manager.get_turn_count() + 1
        agent_user_input = str(user_input.get("raw_text") or user_input.get("selected_choice") or "").strip()
        turn_record = {
            "turn_index": turn_index,
            "user_input": user_input,
            "director_plan": {},
            "dialogues": [],
            "characters": {},
            "director_narrative": {},
            "director_resolve": {},
            "state_changes": {},
        }

        yield {"event": "turn_started", "data": {"turn_index": turn_index, "user_input": user_input}}

        plan: dict[str, Any] | None = None
        for event in self.director.stream_plan(user_input=agent_user_input, state=state_before, logs=logs):
            data = dict(event.get("data", {}))
            if event["event"] == "stage_done" and data.get("stage") == "director_plan":
                plan = ((data.get("payload") or {}).get("director_plan") or {})
                turn_record["director_plan"] = plan
            data["turn_index"] = turn_index
            yield {"event": event["event"], "data": data}

        if plan is None:
            plan = self.director.plan(user_input=agent_user_input, state=state_before, logs=logs).get("director_plan", {})
            turn_record["director_plan"] = plan
        player_input = plan.get("player_input", {})

        ordered_feedback: list[dict[str, Any]] = []
        turn_dialogue: list[dict[str, Any]] = []
        character_index = 0
        for group in self.environment.grouped_characters(plan.get("characters", [])):
            group_entries: list[dict[str, str]] = []
            for character in group:
                character_task = {
                    **character,
                    "player_name": state_before.get("player_display_name", ""),
                    "player_input": player_input,
                    "context": plan.get("context", ""),
                }
                for event in self.environment.stream_character(character_task, state_before, turn_dialogue):
                    data = dict(event.get("data", {}))
                    data["turn_index"] = turn_index
                    data["character_index"] = character_index
                    if event["event"] == "stage_done" and data.get("stage") == "character":
                        character_payload = (data.get("payload", {}) or {}).get("characters", {})
                        character_id = character.get("id", "")
                        character_record = character_payload.get(character_id, {})
                        dialogue = character_record.get("dialogue", {}) if isinstance(character_record, dict) else {}
                        response = dialogue.get("response", "") if isinstance(dialogue, dict) else ""
                        character_feedback = {
                            "character_id": character_id,
                            "name": character_record.get("name", character_id) if isinstance(character_record, dict) else character_id,
                            "order": character.get("order", 1),
                            "response": response,
                            "audience": dialogue.get("audience", []) if isinstance(dialogue, dict) else [],
                        }
                        group_entries.append(self._dialogue_context_entry(character_feedback))
                        ordered_feedback.append(character_feedback)
                        turn_record["dialogues"].append(character_feedback)
                    yield {"event": event["event"], "data": data}
                character_index += 1
            turn_dialogue.extend(group_entries)

        narrative_env_feedback = self._narrative_env_feedback(ordered_feedback)
        env_feedback = self.environment.finalize_feedback(ordered_feedback)

        narrative_result: dict[str, Any] | None = None
        for event in self.director.stream_narrative(
            env_feedback=narrative_env_feedback,
            state=state_before,
            logs=logs,
            user_input=player_input,
            story_guidance=plan.get("story_guidance", ""),
        ):
            data = dict(event.get("data", {}))
            if event["event"] == "stage_done" and data.get("stage") == "director_narrative":
                narrative_result = ((data.get("payload") or {}).get("director_narrative") or {})
                turn_record["director_narrative"] = narrative_result
            data["turn_index"] = turn_index
            yield {"event": event["event"], "data": data}

        if narrative_result is None:
            narrative_result = self.director.narrative(
                env_feedback=narrative_env_feedback,
                state=state_before,
                logs=logs,
                user_input=player_input,
                story_guidance=plan.get("story_guidance", ""),
            ).get("director_narrative", {})
            turn_record["director_narrative"] = narrative_result

        resolve_done = False
        for event in self._stream_resolve_and_reflections(
            ordered_feedback,
            narrative_result,
            state_before,
            logs,
            player_input,
        ):
            data = dict(event.get("data", {}))
            if event["event"] == "stage_done" and data.get("stage") == "director_resolve":
                resolve_done = True
                turn_record["director_resolve"] = ((data.get("payload") or {}).get("director_resolve") or {})
            elif event["event"] == "stage_done" and data.get("stage") == "character_reflection":
                character_id = data.get("character_id", "")
                if character_id:
                    character = ((data.get("payload", {}) or {}).get("characters") or {}).get(character_id, {})
                    if isinstance(character, dict):
                        turn_record["characters"].setdefault(character_id, {}).update(character)
            data["turn_index"] = turn_index
            yield {"event": event["event"], "data": data}

        if not resolve_done:
            turn_record["director_resolve"] = self.director.resolve(
                narrative_result=narrative_result,
                state=state_before,
                logs=logs,
                user_input=player_input,
            ).get("director_resolve", {})

        self._append_event_memories(turn_record, state_before)
        payload = self._finalize_turn(turn_record, env_feedback, state_before)
        yield {"event": "turn_completed", "data": {"turn_index": turn_index, "payload": payload}}

    def _run_dialogue(
        self,
        plan: dict[str, Any],
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        ordered_feedback: list[dict[str, Any]] = []
        turn_dialogue: list[dict[str, Any]] = []
        for group in self.environment.grouped_characters(plan.get("characters", [])):
            group_entries: list[dict[str, str]] = []
            for character in group:
                character_id = character.get("id", "")
                if not character_id:
                    continue
                part = self.environment.character_agent.act(
                    character_id,
                    {
                        **character,
                        "player_name": state.get("player_display_name", ""),
                        "player_input": plan.get("player_input", {}),
                        "context": plan.get("context", ""),
                        "turn_dialogue": list(turn_dialogue),
                    },
                    state,
                )
                character_record = (part.get("characters") or {}).get(character_id, {})
                dialogue = character_record.get("dialogue", {}) if isinstance(character_record, dict) else {}
                response = dialogue.get("response", "") if isinstance(dialogue, dict) else ""
                feedback = {
                    "character_id": character_id,
                    "name": character_record.get("name", character_id) if isinstance(character_record, dict) else character_id,
                    "order": character.get("order", 1),
                    "response": response,
                    "audience": dialogue.get("audience", []) if isinstance(dialogue, dict) else [],
                }
                group_entries.append(self._dialogue_context_entry(feedback))
                ordered_feedback.append(feedback)
            turn_dialogue.extend(group_entries)
        return ordered_feedback

    def _narrative_env_feedback(self, ordered_feedback: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "character_feedback": ordered_feedback,
            "env_summary": " ".join(
                f"{feedback.get('character_id', '')} responded within the scene."
                for feedback in ordered_feedback
                if feedback.get("response")
            ).strip(),
        }

    def _resolve_and_reflect(
        self,
        ordered_feedback: list[dict[str, Any]],
        narrative_result: dict[str, Any],
        state: dict[str, Any],
        logs: dict[str, Any],
        user_input: Any,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        reflection_targets = self._reflection_targets(ordered_feedback)
        with ThreadPoolExecutor(max_workers=max(1, len(reflection_targets) + 1)) as executor:
            resolve_future = executor.submit(
                self.director.resolve,
                narrative_result=narrative_result,
                state=state,
                logs=logs,
                user_input=user_input,
            )
            reflection_futures = {
                executor.submit(
                    self.environment.character_agent.reflect,
                    character_id,
                    self._reflection_context(feedback, narrative_result, state),
                    state,
                ): character_id
                for character_id, feedback in reflection_targets.items()
            }
            reflections = {
                character_id: future.result()
                for future, character_id in reflection_futures.items()
            }
            return resolve_future.result(), reflections

    def _stream_resolve_and_reflections(
        self,
        ordered_feedback: list[dict[str, Any]],
        narrative_result: dict[str, Any],
        state: dict[str, Any],
        logs: dict[str, Any],
        user_input: Any,
    ) -> Iterator[dict[str, Any]]:
        reflection_targets = self._reflection_targets(ordered_feedback)
        factories = [
            lambda: self.director.stream_resolve(
                narrative_result=narrative_result,
                state=state,
                logs=logs,
                user_input=user_input,
            )
        ]
        for character_id, feedback in reflection_targets.items():
            factories.append(
                lambda character_id=character_id, feedback=feedback: self.environment.character_agent.stream_reflect(
                    character_id,
                    self._reflection_context(feedback, narrative_result, state),
                    state,
                )
            )

        event_queue: queue.Queue[tuple[dict[str, Any] | None, BaseException | None]] = queue.Queue()

        def worker(factory) -> None:
            try:
                for event in factory():
                    event_queue.put((event, None))
            except BaseException as error:
                event_queue.put((None, error))
            finally:
                event_queue.put((None, None))

        threads = [threading.Thread(target=worker, args=(factory,), daemon=True) for factory in factories]
        for thread in threads:
            thread.start()

        completed = 0
        while completed < len(threads):
            event, error = event_queue.get()
            if error is not None:
                yield {"event": "stage_error", "data": {"stage": "parallel_resolution", "error": str(error)}}
                completed += 1
            elif event is None:
                completed += 1
            else:
                yield event
        for thread in threads:
            thread.join()

    def _reflection_targets(self, ordered_feedback: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        targets: dict[str, dict[str, Any]] = {}
        for feedback in ordered_feedback:
            character_id = str(feedback.get("character_id", "")).strip()
            if not character_id:
                continue
            if character_id not in targets:
                targets[character_id] = dict(feedback)
                continue
            previous = targets[character_id]
            previous["response"] = "\n".join(
                part
                for part in [previous.get("response", ""), feedback.get("response", "")]
                if part
            )
        return targets

    def _reflection_context(
        self,
        feedback: dict[str, Any],
        narrative_result: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        world = state.get("world", {}) if isinstance(state, dict) else {}
        map_locations = world.get("map_locations", {}) if isinstance(world, dict) else {}
        known_scene_ids = {
            scene_id: scene.get("name", scene_id)
            for scene_id, scene in map_locations.items()
            if isinstance(scene, dict)
        } if isinstance(map_locations, dict) else {}
        return {
            "raw_response": feedback.get("response", ""),
            "final_narrative": narrative_result,
            "known_scene_ids": known_scene_ids,
        }

    def _dialogue_context_entry(self, feedback: dict[str, Any]) -> dict[str, Any]:
        response = str(feedback.get("response") or "")
        entry = {
            "character_id": str(feedback.get("character_id", "")),
            "raw_response": response,
            "visible_dialogue": self.environment.visible_dialogue(response),
        }
        if feedback.get("audience"):
            entry["audience"] = feedback["audience"]
        return entry

    def reset_game(self) -> dict[str, Any]:
        self.bootstrap_runtime()
        self.state_manager.initialize_runtime(reset=True)
        self.state_manager.clear_latest_turn_snapshot()
        self.next_input_mode = INPUT_MODE_HYBRID
        self.next_choices = []
        return self.get_ui_state()

    def save_game(self, slot_id: str) -> dict[str, Any]:
        self.bootstrap_runtime()
        self.state_manager.save(slot_id)
        return self.get_ui_state()

    def load_game(self, slot_id: str) -> dict[str, Any]:
        self.bootstrap_runtime()
        self.state_manager.load(slot_id)
        self._restore_ui_progress_from_logs()
        return self.get_ui_state()

    def delete_save(self, slot_id: str) -> dict[str, Any]:
        self.bootstrap_runtime()
        self.state_manager.delete_save(slot_id)
        return self.get_ui_state()

    def rename_save(self, old_slot_id: str, new_slot_id: str) -> dict[str, Any]:
        self.bootstrap_runtime()
        self.state_manager.rename_save(old_slot_id, new_slot_id)
        return self.get_ui_state()

    def revert_latest_turn(self) -> dict[str, Any]:
        self.bootstrap_runtime()
        self.state_manager.revert_latest_turn()
        self._restore_ui_progress_from_logs()
        return self.get_ui_state()

    def customize_player(self, values: dict[str, Any]) -> dict[str, Any]:
        self.bootstrap_runtime()
        self.state_manager.apply_player_customization(values)
        return self.get_ui_state()

    def run_turn(self) -> bool:
        user_input = self.user_layer.collect_input(self.next_input_mode, self.next_choices)
        raw_text = (user_input.get("raw_text") or "").strip().lower()
        if raw_text in {"quit", "exit", "退出"}:
            return False
        turn_result = self.process_turn(user_input)
        turn_record = turn_result["turn_record"]
        self.user_layer.render_turn(
            {
                **(turn_record.get("director_resolve") or {}),
                **(turn_record.get("director_narrative") or {}),
            }
        )
        return True

    def run(self) -> None:
        self.bootstrap_runtime()
        self.user_layer.render_turn(self.get_opening_payload())
        while self.run_turn():
            pass

    def _restore_ui_progress_from_logs(self) -> None:
        logs = self.state_manager.get_logs(limit=1)
        latest_turn = logs.get("turn_log", [])
        if latest_turn:
            record = latest_turn[-1]
            interaction = record.get("director_resolve", {}).get("interaction", {})
            self.next_input_mode = INPUT_MODE_HYBRID
            self.next_choices = interaction.get("options", [])
        else:
            self.next_input_mode = INPUT_MODE_HYBRID
            self.next_choices = []

    def _finalize_turn(
        self,
        turn_record: dict[str, Any],
        env_feedback: dict[str, Any],
        state_before: dict[str, Any],
    ) -> dict[str, Any]:
        update_result = self.state_manager.apply_update(turn_record)
        director_result = {
            **(turn_record.get("director_resolve") or {}),
            **(turn_record.get("director_narrative") or {}),
        }
        ending = update_result.get("ending", {})
        if ending and not ending.get("narrative"):
            ending_text = self.director.ending(
                ending=ending,
                narrative_result=director_result,
                state=self.state_manager.get_agent_state_view(),
                logs=self.state_manager.get_logs(),
                env_feedback=env_feedback,
            ).get("narrative", "")
            ending = self.state_manager.update_ending_narrative(ending_text) or ending
        turn_record["director_resolve"] = {
            **(turn_record.get("director_resolve") or {}),
            "ending": ending,
        }
        director_result = {
            **(turn_record.get("director_resolve") or {}),
            **(turn_record.get("director_narrative") or {}),
        }
        self.state_manager.append_log(turn_record)
        interaction = director_result.get("interaction", {})
        self.next_input_mode = INPUT_MODE_HYBRID
        self.next_choices = interaction.get("options", [])
        state_after = self.state_manager.get_ui_state_view()
        current_logs = self.state_manager.get_logs(limit=50)
        return {
            "game_id": self.game_id,
            "turn_record": turn_record,
            "state": state_after,
            "ui": {
                "turns": current_logs.get("turn_log", []),
                "messages": current_logs.get("turn_log", []),
                "latest_turn": turn_record,
                "interaction": {"mode": self.next_input_mode, "options": self.next_choices},
                "input_mode": self.next_input_mode,
                "choices": self.next_choices,
                "saves": self.state_manager.list_saves(),
            },
        }

    def _append_event_memories(
        self,
        turn_record: dict[str, Any],
        state_before: dict[str, Any],
    ) -> None:
        request = self.state_manager.story_domain.build_event_memory_request(
            turn_record,
            state_before,
            self.state_manager.static_state.get("story", {}),
        )
        if not request:
            return
        known_characters = state_before.get("characters", {}) if isinstance(state_before.get("characters"), dict) else {}
        character_ids = [item for item in request.get("characters", []) if item in known_characters]
        if not character_ids:
            return

        event_context = {key: value for key, value in request.items() if key != "characters"}
        with ThreadPoolExecutor(max_workers=max(1, len(character_ids))) as executor:
            futures = {
                executor.submit(self.environment.character_agent.remember_event, character_id, event_context, state_before): character_id
                for character_id in character_ids
            }
            for future, character_id in futures.items():
                character = ((future.result().get("characters") or {}).get(character_id) or {})
                if isinstance(character, dict):
                    turn_record.setdefault("characters", {}).setdefault(character_id, {}).update(character)


def build_app(settings: AppConfig) -> GameApp:
    director_llm = _build_llm_client(settings.director)
    character_llm = _build_llm_client(settings.character)
    game_root = settings.games_dir / settings.game_id
    user_game_root = settings.users_dir / settings.user_id / settings.game_id
    state_manager = StateManager(game_root=game_root, user_game_root=user_game_root)
    character_agent = CharacterAgent(
        llm_client=character_llm,
        thinking=settings.character.thinking,
        state_manager=state_manager,
    )
    environment = EnvironmentLayer(character_agent=character_agent)
    director = DirectorAgent(
        llm_client=director_llm,
        thinking=settings.director.thinking,
        state_manager=state_manager,
    )
    user_layer = UserLayer()
    return GameApp(
        game_id=settings.game_id,
        user_layer=user_layer,
        director=director,
        environment=environment,
        state_manager=state_manager,
    )


def _build_llm_client(config: AgentLLMConfig) -> OpenAIClient | None:
    if not config.enabled or not config.api_key:
        return None
    return OpenAIClient(
        api_key=config.api_key,
        model=config.model,
        base_url=config.base_url,
        max_retries=config.max_retries,
        retry_base_delay=config.retry_base_delay,
        thinking=config.thinking,
    )


def main() -> None:
    settings = load_app_config()
    app = build_app(settings)
    app.run()


if __name__ == "__main__":
    main()
