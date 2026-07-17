"""In-memory pipeline workflow state for the frontend stepper."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class StepStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


STEPS = [
    {"id": "upload", "label": "Upload Documents", "order": 1},
    {"id": "chunk", "label": "Chunk Preview", "order": 2},
    {"id": "index", "label": "Embed & Index", "order": 3},
    {"id": "evaluate", "label": "Run Evaluation", "order": 4},
    {"id": "compare", "label": "Compare Models", "order": 5},
]


class PipelineState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.steps: dict[str, dict[str, Any]] = {
                step["id"]: {
                    "id": step["id"],
                    "label": step["label"],
                    "order": step["order"],
                    "status": StepStatus.IDLE.value,
                    "message": "",
                    "updated_at": None,
                }
                for step in STEPS
            }
            self.logs: list[dict[str, str]] = []
            self.tenant_id: str = ""
            self.chunk_count: int = 0
            self.indexed_models: list[str] = []
            self.evaluated_models: list[str] = []
            self.current_job: str | None = None

    def set_step(
        self,
        step_id: str,
        status: StepStatus,
        message: str = "",
    ) -> None:
        with self._lock:
            if step_id in self.steps:
                self.steps[step_id]["status"] = status.value
                self.steps[step_id]["message"] = message
                self.steps[step_id]["updated_at"] = datetime.now(timezone.utc).isoformat()

    def add_log(self, message: str, level: str = "info") -> None:
        with self._lock:
            self.logs.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": level,
                    "message": message,
                }
            )
            if len(self.logs) > 200:
                self.logs = self.logs[-200:]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "steps": list(self.steps.values()),
                "logs": list(self.logs[-50:]),
                "tenant_id": self.tenant_id,
                "chunk_count": self.chunk_count,
                "indexed_models": list(self.indexed_models),
                "evaluated_models": list(self.evaluated_models),
                "current_job": self.current_job,
            }


pipeline_state = PipelineState()
