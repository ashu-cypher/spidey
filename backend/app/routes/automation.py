"""Automation Engine API — manage scheduled jobs.

* ``GET /api/automation/jobs`` — list jobs
* ``POST /api/automation/jobs`` — create {kind, name, schedule, payload, enabled}
* ``DELETE /api/automation/jobs/{id}`` — delete
* ``POST /api/automation/jobs/{id}/run`` — manual trigger (runs now, honest result)

Schedules: ``daily@HH:MM``, ``weekly@DAY@HH:MM``, ``interval@MINUTES``,
``once@<ISO-8601>``. Errors are structured {"error": ...}; a manual run
never claims a briefing was sent unless the channel API confirmed it.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import scheduler

router = APIRouter()


class JobCreate(BaseModel):
    kind: str = Field(description="daily_briefing | news_briefing | weekly_review | reminder")
    name: str
    schedule: str = Field(
        description="daily@HH:MM | weekly@DAY@HH:MM | interval@MINUTES | once@<ISO-8601>"
    )
    payload: dict = Field(default_factory=dict)
    enabled: bool = True


@router.get("/api/automation/jobs")
async def list_automation_jobs():
    return {"jobs": scheduler.list_jobs()}


@router.post("/api/automation/jobs", status_code=201)
async def create_automation_job(body: JobCreate):
    try:
        job = scheduler.create_job(
            kind=body.kind,
            name=body.name,
            schedule=body.schedule,
            payload=body.payload,
            enabled=body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    return {"job": job}


@router.delete("/api/automation/jobs/{job_id}")
async def delete_automation_job(job_id: str):
    if not scheduler.delete_job(job_id):
        raise HTTPException(
            status_code=404, detail={"error": f"Job {job_id!r} not found."}
        )
    return {"deleted": job_id}


@router.post("/api/automation/jobs/{job_id}/run")
async def run_automation_job(job_id: str):
    result = await scheduler.run_job_now(job_id)
    if result.get("error") in ("Job not found.",):
        raise HTTPException(status_code=404, detail={"error": result["error"]})
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail={"error": result.get("error")})
    return result
