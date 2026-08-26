from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class DomainContext:
    config: dict[str, Any]
    static_state: dict[str, Any]
    runtime_dir: Path
    turn_number: int
    condition_met: Callable[[Any, Any], bool]
    coerce_value: Callable[[Any], Any]
    record_change: Callable[[dict[str, Any], tuple[str, ...], Any, Any, str], None]
