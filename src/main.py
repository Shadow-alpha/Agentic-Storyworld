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
    director: AgentLLMConfig
    character: AgentLLMConfig
    debug: bool

    def with_game_id(self, game_id: str) -> "AppConfig":
        return replace(self, game_id=game_id)

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
    return AppConfig(
        game_id=str(game_config.get("default_game_id") or "demo_game"),
        games_dir=_resolve_path(project_root, game_config.get("games_dir") or "games"),
        director=read_llm_config(_agent_llm_block(config, "director")),
        character=read_llm_config(_agent_llm_block(config, "character")),
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
            "narrative": {"visible": opening_text, "hidden": ""},
            "goal": {},
            "interaction": {"mode": self.next_input_mode, "options": []},
            "state_update": {},
        }

    def get_ui_state(self, log_limit: int | None = 50) -> dict[str, Any]:
        state = self.get_current_state()
        logs = self.get_current_logs(limit=log_limit)
        turns = self._build_ui_turns(logs)
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
        state_before = self.state_manager.get_agent_state_view()
        logs = self.state_manager.get_logs()
        agent_user_input = self._agent_user_input(user_input)
        plan = self.director.plan(user_input=agent_user_input, state=state_before, logs=logs)
        ordered_feedback = self._run_dialogue(plan, state_before)
        narrative_env_feedback = self._narrative_env_feedback(ordered_feedback)
        env_feedback = self.environment.finalize_feedback(ordered_feedback)
        narrative_result = self.director.narrative(
            env_feedback=narrative_env_feedback,
            state=state_before,
            logs=logs,
            user_input=agent_user_input,
        )
        resolve_result, reflections = self._resolve_and_reflect(
            ordered_feedback,
            narrative_result,
            state_before,
            logs,
            agent_user_input,
        )
        self._merge_reflections(ordered_feedback, reflections)
        env_feedback = self.environment.finalize_feedback(ordered_feedback)
        director_result = {**resolve_result, **narrative_result}
        return self._finalize_turn(user_input, plan, env_feedback, director_result)

    def stream_turn(self, user_input: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self.bootstrap_runtime()
        state_before = self.state_manager.get_agent_state_view()
        logs = self.state_manager.get_logs()
        turn_index = self.state_manager.get_turn_count() + 1
        agent_user_input = self._agent_user_input(user_input)

        yield {"event": "turn_started", "data": {"turn_index": turn_index, "user_input": user_input}}

        plan: dict[str, Any] | None = None
        for event in self.director.stream_plan(user_input=agent_user_input, state=state_before, logs=logs):
            data = dict(event.get("data", {}))
            if event["event"] == "stage_done" and data.get("stage") == "director_plan":
                plan = data["plan"]
            data["turn_index"] = turn_index
            yield {"event": event["event"], "data": data}

        if plan is None:
            plan = self.director.plan(user_input=agent_user_input, state=state_before, logs=logs)

        ordered_feedback: list[dict[str, Any]] = []
        turn_dialogue: list[dict[str, str]] = []
        character_index = 0
        for group in self.environment.grouped_characters(plan.get("characters", [])):
            group_entries: list[dict[str, str]] = []
            for character in group:
                character_feedback: dict[str, Any] | None = None
                for event in self.environment.stream_character(character, state_before, turn_dialogue):
                    data = dict(event.get("data", {}))
                    data["turn_index"] = turn_index
                    data["character_index"] = character_index
                    if event["event"] == "stage_done" and data.get("stage") == "character":
                        character_feedback = data["character_feedback"]
                        character_feedback["order"] = character.get("order", 1)
                        character_feedback["raw_response"] = character_feedback.get("response", "")
                        character_feedback["visible_dialogue"] = self.environment.visible_dialogue(
                            character_feedback.get("response", "")
                        )
                        group_entries.append(self.environment.dialogue_entry(character_feedback))
                        ordered_feedback.append(character_feedback)
                    yield {"event": event["event"], "data": data}
                character_index += 1
            turn_dialogue.extend(group_entries)

        narrative_env_feedback = self._narrative_env_feedback(ordered_feedback)
        env_feedback = self.environment.finalize_feedback(ordered_feedback)
        yield {
            "event": "stage_done",
            "data": {"turn_index": turn_index, "stage": "environment", "env_feedback": env_feedback},
        }

        narrative_result: dict[str, Any] | None = None
        for event in self.director.stream_narrative(
            env_feedback=narrative_env_feedback,
            state=state_before,
            logs=logs,
            user_input=agent_user_input,
        ):
            data = dict(event.get("data", {}))
            if event["event"] == "stage_done" and data.get("stage") == "director_narrative":
                narrative_result = data["narrative_result"]
            data["turn_index"] = turn_index
            yield {"event": event["event"], "data": data}

        if narrative_result is None:
            narrative_result = self.director.narrative(
                env_feedback=narrative_env_feedback,
                state=state_before,
                logs=logs,
                user_input=agent_user_input,
            )

        resolve_result: dict[str, Any] | None = None
        reflections: dict[str, dict[str, Any]] = {}
        for event in self._stream_resolve_and_reflections(
            ordered_feedback,
            narrative_result,
            state_before,
            logs,
            agent_user_input,
        ):
            data = dict(event.get("data", {}))
            if event["event"] == "stage_done" and data.get("stage") == "director_resolve":
                resolve_result = data["resolve_result"]
            elif event["event"] == "stage_done" and data.get("stage") == "character_reflection":
                character_id = data.get("character_id", "")
                if character_id:
                    reflections[character_id] = data.get("character_reflection", {})
            data["turn_index"] = turn_index
            yield {"event": event["event"], "data": data}

        if resolve_result is None:
            resolve_result = self.director.resolve(
                narrative_result=narrative_result,
                state=state_before,
                logs=logs,
                user_input=agent_user_input,
            )

        self._merge_reflections(ordered_feedback, reflections)
        env_feedback = self.environment.finalize_feedback(ordered_feedback)
        director_result = {**resolve_result, **narrative_result}
        payload = self._finalize_turn(user_input, plan, env_feedback, director_result)
        yield {"event": "turn_completed", "data": {"turn_index": turn_index, "payload": payload}}

    def _run_dialogue(self, plan: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
        ordered_feedback: list[dict[str, Any]] = []
        turn_dialogue: list[dict[str, str]] = []
        for group in self.environment.grouped_characters(plan.get("characters", [])):
            group_entries: list[dict[str, str]] = []
            for character in group:
                feedback = self.environment.run_character(character, state, turn_dialogue)
                group_entries.append(self.environment.dialogue_entry(feedback))
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
        user_input: str,
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
                    self._reflection_context(feedback, narrative_result),
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
        user_input: str,
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
                    self._reflection_context(feedback, narrative_result),
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
            previous["raw_response"] = "\n".join(
                part
                for part in [previous.get("raw_response", ""), feedback.get("raw_response", feedback.get("response", ""))]
                if part
            )
        return targets

    def _reflection_context(self, feedback: dict[str, Any], narrative_result: dict[str, Any]) -> dict[str, Any]:
        return {
            "raw_response": feedback.get("raw_response") or feedback.get("response", ""),
            "final_narrative": narrative_result,
        }

    def _merge_reflections(
        self,
        ordered_feedback: list[dict[str, Any]],
        reflections: dict[str, dict[str, Any]],
    ) -> None:
        for feedback in ordered_feedback:
            character_id = feedback.get("character_id", "")
            reflection = reflections.get(character_id)
            if not reflection:
                continue
            feedback["emotion"] = reflection.get("emotion", feedback.get("emotion", ""))
            feedback["state_update"] = reflection.get("state_update", {})
            feedback["memory_append"] = reflection.get("memory_append", "")

    def reset_game(self) -> dict[str, Any]:
        self.bootstrap_runtime()
        self.state_manager.initialize_runtime(reset=True)
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

    def customize_player(self, values: dict[str, Any]) -> dict[str, Any]:
        self.bootstrap_runtime()
        self.state_manager.apply_player_customization(values)
        return self.get_ui_state()

    def activate_goal(self, goal_id: str) -> dict[str, Any]:
        self.bootstrap_runtime()
        self.state_manager.activate_goal(goal_id)
        return self.get_ui_state()

    def deactivate_goal(self, goal_id: str) -> dict[str, Any]:
        self.bootstrap_runtime()
        self.state_manager.deactivate_goal(goal_id)
        return self.get_ui_state()

    def run_turn(self) -> bool:
        user_input = self.user_layer.collect_input(self.next_input_mode, self.next_choices)
        raw_text = (user_input.get("raw_text") or "").strip().lower()
        if raw_text in {"quit", "exit", "退出"}:
            return False
        turn_result = self.process_turn(user_input)
        self.user_layer.render_turn(turn_result["director_result"])
        return True

    def _agent_user_input(self, user_input: dict[str, Any]) -> str:
        return str(user_input.get("raw_text") or user_input.get("selected_choice") or "").strip()

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
            interaction = self._director_result_from_log(record).get("interaction", {})
            self.next_input_mode = INPUT_MODE_HYBRID
            self.next_choices = interaction.get("options", [])
        else:
            self.next_input_mode = INPUT_MODE_HYBRID
            self.next_choices = []

    def _build_ui_turns(self, logs: dict[str, Any]) -> list[dict[str, Any]]:
        turns: list[dict[str, Any]] = []
        for index, record in enumerate(logs.get("turn_log", [])):
            director_result = self._director_result_from_log(record)
            turns.append(
                {
                    "turn_index": record.get("turn_index", index + 1),
                    "timestamp": record.get("timestamp"),
                    "user_input": record.get("user_input", {}),
                    "plan": record.get("plan", {}),
                    "env_feedback": {"character_feedback": record.get("characters", [])},
                    "director_result": director_result,
                }
            )
        return turns

    def _director_result_from_log(self, record: dict[str, Any]) -> dict[str, Any]:
        """Return the current director-result log payload, with old-log fallback."""
        source = record.get("director_result")
        if not isinstance(source, dict):
            source = record.get("integrate", {})
        if not isinstance(source, dict):
            source = {}
        interaction = source.get("interaction", {})
        return {
            "time": source.get("time", ""),
            "scene": source.get("scene", ""),
            "narrative": source.get("narrative", {}),
            "summary": source.get("summary", ""),
            "movement": source.get("movement", []),
            "goal_update": source.get("goal_update", {}),
            "goal_resolution": source.get("goal_resolution", {}),
            "ending": source.get("ending", {}),
            "interaction": interaction if isinstance(interaction, dict) else {},
            "state_update": source.get("state_update", {}),
        }

    def _compact_plan_for_log(self, plan: dict[str, Any]) -> dict[str, Any]:
        characters = []
        for character in plan.get("characters", []):
            if not isinstance(character, dict):
                continue
            characters.append(
                {
                    "id": character.get("id", ""),
                    "name": character.get("name", character.get("id", "")),
                    "order": character.get("order", 1),
                }
            )
        return {
            "user_intent": plan.get("user_intent", ""),
            "context": plan.get("context", ""),
            "characters": characters,
        }

    def _compact_character_feedback_for_log(
        self,
        env_feedback: dict[str, Any],
        state_changes: dict[str, Any],
    ) -> list[dict[str, Any]]:
        characters = []
        character_changes = state_changes.get("characters", {}) if isinstance(state_changes, dict) else {}
        for feedback in env_feedback.get("character_feedback", []):
            if not isinstance(feedback, dict):
                continue
            character_id = feedback.get("character_id", "")
            characters.append(
                {
                    "character_id": character_id,
                    "name": feedback.get("name", character_id),
                    "order": feedback.get("order", 1),
                    "response": feedback.get("response", ""),
                    "emotion": feedback.get("emotion", ""),
                    "state_update": character_changes.get(character_id, {}),
                    "memory_append": feedback.get("memory_append", ""),
                }
            )
        return characters

    def _compact_director_result_for_log(
        self,
        director_result: dict[str, Any],
        state_changes: dict[str, Any],
    ) -> dict[str, Any]:
        interaction = director_result.get("interaction", {})
        return {
            "time": director_result.get("time", ""),
            "scene": director_result.get("scene", ""),
            "narrative": director_result.get("narrative", {}),
            "summary": director_result.get("summary", ""),
            "movement": director_result.get("movement", []),
            "goal_update": director_result.get("goal_update", {}),
            "goal_resolution": director_result.get("goal_resolution", {}),
            "ending": director_result.get("ending", {}),
            "state_update": {
                "world_state": state_changes.get("world_state", {}),
                "user_state": state_changes.get("user_state", {}),
            },
            "interaction": {
                "mode": INPUT_MODE_HYBRID,
                "options": interaction.get("options", []) if isinstance(interaction, dict) else [],
            },
        }

    def _finalize_turn(
        self,
        user_input: dict[str, Any],
        plan: dict[str, Any],
        env_feedback: dict[str, Any],
        director_result: dict[str, Any],
    ) -> dict[str, Any]:
        character_updates = {
            item.get("character_id"): {
                "emotion": item.get("emotion", ""),
                "state_update": item.get("state_update", {}),
                "memory_append": item.get("memory_append", ""),
            }
            for item in env_feedback.get("character_feedback", [])
            if item.get("character_id")
        }
        for movement in director_result.get("movement", []):
            if not isinstance(movement, dict):
                continue
            character_id = movement.get("character_id", "")
            location = movement.get("location", "")
            if not character_id or not location:
                continue
            update = character_updates.setdefault(
                character_id,
                {"emotion": "", "state_update": {}, "memory_append": ""},
            )
            update.setdefault("state_update", {})["location"] = {
                "value": location,
                "reason": movement.get("reason", ""),
            }
        state_update = self._state_update_with_narrative_location(director_result)
        state_changes = self.state_manager.apply_state_update(
            {
                **state_update,
                "characters": character_updates,
            }
        )
        goal_update_result = self.state_manager.apply_goal_update(director_result.get("goal_update", {}))
        ending = self.state_manager.check_endings()
        director_result = dict(director_result)
        director_result["goal_update"] = {
            "checkpoints": goal_update_result.get("checkpoints", []),
        }
        director_result["goal_resolution"] = {
            "completed_goals": goal_update_result.get("completed_goals", []),
            "available_goals": goal_update_result.get("available_goals", []),
        }
        director_result["ending"] = ending
        turn_index = self.state_manager.get_turn_count() + 1
        turn_record = {
            "turn_index": turn_index,
            "user_input": user_input,
            "plan": self._compact_plan_for_log(plan),
            "characters": self._compact_character_feedback_for_log(env_feedback, state_changes),
            "director_result": self._compact_director_result_for_log(director_result, state_changes),
        }
        self.state_manager.append_log(turn_record)
        interaction = director_result.get("interaction", {})
        self.next_input_mode = INPUT_MODE_HYBRID
        self.next_choices = interaction.get("options", [])
        state_after = self.state_manager.get_ui_state_view()
        current_logs = self.state_manager.get_logs(limit=50)
        return {
            "game_id": self.game_id,
            "plan": plan,
            "env_feedback": env_feedback,
            "director_result": director_result,
            "state": state_after,
            "ui": {
                "turns": self._build_ui_turns(current_logs),
                "messages": current_logs.get("turn_log", []),
                "latest_turn": turn_record,
                "interaction": {"mode": self.next_input_mode, "options": self.next_choices},
                "input_mode": self.next_input_mode,
                "choices": self.next_choices,
                "saves": self.state_manager.list_saves(),
            },
        }

    def _state_update_with_narrative_location(self, director_result: dict[str, Any]) -> dict[str, Any]:
        state_update = dict(director_result.get("state_update") or {})
        scene = director_result.get("scene", {})
        scene_id = scene.get("id", "") if isinstance(scene, dict) else ""
        time_text = str(director_result.get("time") or "").strip()
        if scene_id:
            user_state = dict(state_update.get("user_state") or {})
            user_state.setdefault("location", scene_id)
            state_update["user_state"] = user_state
        if time_text:
            world_state = dict(state_update.get("world_state") or {})
            world_state.setdefault("time", time_text)
            state_update["world_state"] = world_state
        return state_update


def build_app(settings: AppConfig) -> GameApp:
    director_llm = _build_llm_client(settings.director)
    character_llm = _build_llm_client(settings.character)
    game_root = settings.games_dir / settings.game_id
    state_manager = StateManager(game_root=game_root)
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
