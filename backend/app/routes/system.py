"""MISSION MEW — system metrics route (read-only).

GET /api/system/metrics instantiates the SystemControllerTool directly with
``action="metrics"`` — no confirmation is needed because metrics are
read-only. Never exposed: the ``run``/``open_website`` actions.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.tools.system_controller import SystemControllerTool

router = APIRouter()


@router.get("/api/system/metrics")
async def system_metrics():
    tool = SystemControllerTool()
    return await tool.execute(action="metrics")
