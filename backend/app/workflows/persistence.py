"""Phase 7 — workflow persistence (DB write-through for the in-memory engine).

The engine keeps serving live SSE from memory; ``persist_run`` is registered
as a finish hook (see ``app.routes.chat``) and upserts the finished run plus
all its steps into ``workflow_runs`` / ``workflow_steps``. Step input/output
is stored as JSON truncated at ~2000 serialized chars (debug mode does not
change this — it only affects the live in-memory response).

``get_persisted_run`` / ``list_persisted_runs`` return engine-shaped dicts so
the routes can merge memory + DB transparently.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import delete, select

from app.database import get_session
from app.models import WorkflowRun as WorkflowRunRow
from app.models import WorkflowStep as WorkflowStepRow

logger = logging.getLogger("spidey")

_STEP_JSON_LIMIT = 2000  # serialized chars for stored step input/output


def _truncate_json(value) -> dict | None:
    """Store JSON, truncated at ~2000 serialized chars (Phase 7 spec)."""
    if value is None:
        return None
    serialized = json.dumps(value, default=str, ensure_ascii=False)
    if len(serialized) <= _STEP_JSON_LIMIT:
        return json.loads(serialized)
    return {"_truncated": True, "preview": serialized[:_STEP_JSON_LIMIT]}


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError, TypeError):
        return None


def _dt_iso(value) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def persist_run(run) -> None:
    """Upsert a finished engine run + its steps. Never raises."""
    try:
        with get_session() as session:
            row = session.get(WorkflowRunRow, run.workflow_id)
            if row is None:
                row = WorkflowRunRow(
                    id=run.workflow_id,
                    user_id=run.user_id,
                    request=run.request,
                    status=run.status,
                    started_at=_parse_dt(run.started_at) or datetime.utcnow(),
                    completed_at=_parse_dt(run.completed_at),
                    result=run.result,
                )
                session.add(row)
            else:
                row.status = run.status
                row.completed_at = _parse_dt(run.completed_at)
                row.result = run.result
            # Rewrite steps wholesale — a re-finished run (e.g. the
            # _execute safety net) must not duplicate them.
            session.execute(
                delete(WorkflowStepRow).where(
                    WorkflowStepRow.workflow_run_id == run.workflow_id
                )
            )
            for seq, step in enumerate(run.steps):
                session.add(
                    WorkflowStepRow(
                        workflow_run_id=run.workflow_id,
                        name=step.name,
                        type=step.type,
                        status=step.status.lower(),
                        seq=seq,
                        input=_truncate_json(step.input),
                        output=_truncate_json(step.output),
                        error=step.error,
                        started_at=_parse_dt(step.started_at),
                        completed_at=_parse_dt(step.completed_at),
                    )
                )
    except Exception:
        logger.exception("persist_run failed for %s", getattr(run, "workflow_id", "?"))


def _row_to_dict(row: WorkflowRunRow, steps: list[WorkflowStepRow]) -> dict:
    return {
        "workflow_id": row.id,
        "user_id": row.user_id,
        "request": row.request,
        "status": row.status,
        "started_at": _dt_iso(row.started_at),
        "completed_at": _dt_iso(row.completed_at),
        "result": row.result,
        "steps": [
            {
                "step_id": s.id,
                "workflow_run_id": s.workflow_run_id,
                "name": s.name,
                "type": s.type,
                "status": (s.status or "waiting").upper(),
                "input": s.input or {},
                "output": s.output or {},
                "started_at": _dt_iso(s.started_at),
                "completed_at": _dt_iso(s.completed_at),
                "error": s.error,
            }
            for s in steps
        ],
    }


def get_persisted_run(run_id: str) -> dict | None:
    """Reconstruct the engine response shape from the DB, or None."""
    try:
        with get_session() as session:
            row = session.get(WorkflowRunRow, run_id)
            if row is None:
                return None
            steps = list(
                session.scalars(
                    select(WorkflowStepRow)
                    .where(WorkflowStepRow.workflow_run_id == run_id)
                    .order_by(WorkflowStepRow.seq)
                )
            )
            return _row_to_dict(row, steps)
    except Exception:
        logger.exception("get_persisted_run failed for %s", run_id)
        return None


def list_persisted_runs(limit: int = 200) -> list[dict]:
    """DB-backed runs, newest first, in the engine response shape."""
    try:
        with get_session() as session:
            rows = list(
                session.scalars(
                    select(WorkflowRunRow)
                    .order_by(WorkflowRunRow.started_at.desc())
                    .limit(limit)
                )
            )
            out = []
            for row in rows:
                steps = list(
                    session.scalars(
                        select(WorkflowStepRow)
                        .where(WorkflowStepRow.workflow_run_id == row.id)
                        .order_by(WorkflowStepRow.seq)
                    )
                )
                out.append(_row_to_dict(row, steps))
            return out
    except Exception:
        logger.exception("list_persisted_runs failed")
        return []
