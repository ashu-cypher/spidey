"""MISSION MEW — protocols API.

GET  /api/protocols          -> the five house protocols (id, name,
                               description, sfx).
POST /api/protocols/trigger -> engage a protocol: scripted MEW response,
                               an audit row, and a "protocol" event on the
                               event bus. Unknown ids -> 404.
GET  /api/protocols/audit    -> last 50 audit entries, newest first.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.database import get_session
from app.models import ProtocolAudit
from app.services.event_bus import publish

router = APIRouter()

PROTOCOLS: list[dict] = [
    {
        "id": "clean_slate",
        "name": "Clean Slate",
        "description": (
            "A fresh start. All non-essential systems stand down, "
            "caches are cleared, and the house returns to a pristine, "
            "ready state — as if the day had just begun."
        ),
        "sfx": "protocol_alert",
        "response_text": (
            "Very well. Wiping the slate clean — all non-essential "
            "systems standing down, house restored to ready state."
        ),
    },
    {
        "id": "house_party",
        "name": "House Party Protocol",
        "description": (
            "Entertainment mode. Lights dim to a warm glow, the music "
            "queue opens, and the house prepares to receive guests in style."
        ),
        "sfx": "protocol_alert",
        "response_text": (
            "House party protocol initiated. Dimming the lights and "
            "queuing the playlist — do try to enjoy yourself."
        ),
    },
    {
        "id": "sentry_mode",
        "name": "Sentry Mode",
        "description": (
            "Maximum vigilance. All sensors at full alert, perimeter "
            "monitoring engaged, and I shall keep a very close eye on things."
        ),
        "sfx": "protocol_alert",
        "response_text": (
            "Sentry mode engaged. All sensors at full alert."
        ),
    },
    {
        "id": "mute_audio",
        "name": "Mute Audio",
        "description": (
            "Silence. All house audio output is muted at once — "
            "useful when discretion is the better part of valor."
        ),
        "sfx": "hud_blip",
        "response_text": (
            "Audio muted. Silent as a whisper."
        ),
    },
    {
        "id": "silent_running",
        "name": "Silent Running",
        "description": (
            "Minimal emissions. Background activity throttled, "
            "notifications hushed, and the house moves like a shadow."
        ),
        "sfx": "protocol_alert",
        "response_text": (
            "Silent running. Minimal emissions, maximum discretion."
        ),
    },
]

_PROTOCOLS_BY_ID = {p["id"]: p for p in PROTOCOLS}

_AUDIT_LIMIT = 50


class TriggerRequest(BaseModel):
    id: str = ""


def _public(protocol: dict) -> dict:
    return {
        "id": protocol["id"],
        "name": protocol["name"],
        "description": protocol["description"],
        "sfx": protocol["sfx"],
    }


@router.get("/api/protocols")
async def list_protocols():
    return [_public(p) for p in PROTOCOLS]


@router.post("/api/protocols/trigger")
async def trigger_protocol(req: TriggerRequest):
    protocol = _PROTOCOLS_BY_ID.get((req.id or "").strip())
    if protocol is None:
        raise HTTPException(404, f"Unknown protocol: {req.id!r}.")
    with get_session() as session:
        row = ProtocolAudit(
            protocol_id=protocol["id"],
            protocol_name=protocol["name"],
            response_text=protocol["response_text"],
        )
        session.add(row)
        session.flush()
        audit_id = row.id
    publish(
        {
            "type": "protocol",
            "id": protocol["id"],
            "name": protocol["name"],
        }
    )
    return {
        "id": protocol["id"],
        "name": protocol["name"],
        "response_text": protocol["response_text"],
        "sfx": protocol["sfx"],
        "audit_id": audit_id,
    }


@router.get("/api/protocols/audit")
async def protocol_audit():
    with get_session() as session:
        rows = session.execute(
            select(ProtocolAudit)
            .order_by(ProtocolAudit.triggered_at.desc())
            .limit(_AUDIT_LIMIT)
        ).scalars().all()
        return [
            {
                "id": r.id,
                "protocol_id": r.protocol_id,
                "protocol_name": r.protocol_name,
                "response_text": r.response_text,
                "triggered_at": r.triggered_at.isoformat()
                if r.triggered_at
                else None,
            }
            for r in rows
        ]
