import asyncio
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowStep(BaseModel):
    step_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    workflow_run_id: str
    name: str
    type: str
    status: str = "WAITING"  # WAITING/RUNNING/COMPLETED/FAILED
    input: dict = Field(default_factory=dict)
    output: dict = Field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None


class WorkflowRun(BaseModel):
    workflow_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    user_id: str = "local"
    request: str
    status: str = "running"  # running/completed/failed
    started_at: str = Field(default_factory=_now)
    completed_at: str | None = None
    result: str | None = None
    steps: list[WorkflowStep] = Field(default_factory=list)


class WorkflowEngine:
    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRun] = {}
        self._queues: dict[str, list[asyncio.Queue]] = {}

    def create_run(self, request: str, user_id: str = "local") -> WorkflowRun:
        run = WorkflowRun(request=request, user_id=user_id)
        self._runs[run.workflow_id] = run
        return run

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    def list_runs(self) -> list[WorkflowRun]:
        return sorted(
            self._runs.values(), key=lambda r: r.started_at, reverse=True
        )

    def add_step(
        self,
        run_id: str,
        name: str,
        type: str,
        input_data: dict | None = None,
    ) -> WorkflowStep:
        run = self._runs[run_id]
        step = WorkflowStep(
            workflow_run_id=run_id,
            name=name,
            type=type,
            input=input_data or {},
        )
        run.steps.append(step)
        self._publish(
            run_id, {"type": "step_update", "step": step.model_dump(mode="json")}
        )
        return step

    def update_step(
        self,
        run_id: str,
        step_id: str,
        status: str | None = None,
        output: dict | None = None,
        error: str | None = None,
    ) -> WorkflowStep:
        run = self._runs[run_id]
        step = next(s for s in run.steps if s.step_id == step_id)
        if status:
            step.status = status
            if status == "RUNNING" and not step.started_at:
                step.started_at = _now()
            if status in ("COMPLETED", "FAILED"):
                step.completed_at = _now()
        if output is not None:
            step.output = output
        if error is not None:
            step.error = error
        self._publish(
            run_id, {"type": "step_update", "step": step.model_dump(mode="json")}
        )
        return step

    def finish_run(
        self, run_id: str, status: str, result: str | None = None
    ) -> WorkflowRun:
        run = self._runs[run_id]
        run.status = status
        run.completed_at = _now()
        run.result = result
        self._publish(
            run_id, {"type": "done", "run": run.model_dump(mode="json")}
        )
        return run

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues.setdefault(run_id, []).append(q)
        return q

    def _publish(self, run_id: str, event: dict) -> None:
        for q in self._queues.get(run_id, []):
            q.put_nowait(event)


engine = WorkflowEngine()  # process singleton
