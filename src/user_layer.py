from __future__ import annotations

from typing import Any

from .schemas import INPUT_MODE_CHOICE, INPUT_MODE_FREE, INPUT_MODE_HYBRID


class UserLayer:
    """Collects user input and renders the visible turn output."""

    def __init__(self) -> None:
        self.last_input_mode: str | None = None
        self.last_choices: list[dict[str, Any]] = []

    def render_turn(self, director_result: dict[str, Any]) -> None:
        narrative = director_result.get("narrative", {})
        if isinstance(narrative, dict):
            visible = narrative.get("visible", "").strip()
        else:
            visible = str(narrative).strip()
        if visible:
            print("\n" + visible + "\n")
        interaction = director_result.get("interaction", {})
        choices = interaction.get("options", [])
        if choices:
            print("可选行动:")
            for index, choice in enumerate(choices, start=1):
                print(f"  {index}. {choice['text']}")
            print()
        self.last_input_mode = interaction.get("mode")
        self.last_choices = choices

    def collect_input(self, input_mode: str, choices: list[dict[str, Any]]) -> dict[str, Any]:
        self.last_input_mode = input_mode
        self.last_choices = choices
        if input_mode == INPUT_MODE_CHOICE:
            return self._collect_choice_input(choices)
        if input_mode == INPUT_MODE_HYBRID:
            return self._collect_hybrid_input(choices)
        return self._collect_free_input()

    def _collect_free_input(self) -> dict[str, Any]:
        raw_text = input("你: ").strip()
        return {
            "input_mode": INPUT_MODE_FREE,
            "raw_text": raw_text,
            "choice_id": None,
            "selected_choice": None,
            "meta": {},
        }

    def _collect_choice_input(self, choices: list[dict[str, Any]]) -> dict[str, Any]:
        while True:
            raw = input("请选择编号: ").strip()
            if raw.isdigit():
                index = int(raw) - 1
                if 0 <= index < len(choices):
                    selected = choices[index]
                    return {
                        "input_mode": INPUT_MODE_CHOICE,
                        "raw_text": None,
                        "choice_id": selected["id"],
                        "selected_choice": selected["text"],
                        "meta": {},
                    }
            print("输入无效，请重新输入选项编号。")

    def _collect_hybrid_input(self, choices: list[dict[str, Any]]) -> dict[str, Any]:
        print("你可以输入选项编号，也可以直接输入自然语言；若两者都想提供，请先写编号再补充文本。")
        raw = input("你: ").strip()
        if raw.isdigit():
            index = int(raw) - 1
            if 0 <= index < len(choices):
                selected = choices[index]
                return {
                    "input_mode": INPUT_MODE_HYBRID,
                    "raw_text": selected["text"],
                    "choice_id": selected["id"],
                    "selected_choice": selected["text"],
                    "meta": {},
                }
        parts = raw.split(maxsplit=1)
        if parts and parts[0].isdigit():
            index = int(parts[0]) - 1
            if 0 <= index < len(choices):
                selected = choices[index]
                return {
                    "input_mode": INPUT_MODE_HYBRID,
                    "raw_text": parts[1] if len(parts) > 1 else selected["text"],
                    "choice_id": selected["id"],
                    "selected_choice": selected["text"],
                    "meta": {},
                }
        return {
            "input_mode": INPUT_MODE_HYBRID,
            "raw_text": raw,
            "choice_id": None,
            "selected_choice": None,
            "meta": {},
        }
