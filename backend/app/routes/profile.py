"""MEW Phase 2 — user profile store (spec section 8).

``GET /api/profile`` — the stored profile (all fields user-supplied;
unknown fields are null, never invented).

``PUT /api/profile`` — partial update: only the fields present in the
request body are written. ``null`` for a scalar clears it; JSON lists are
replaced wholesale (no merging magic the user didn't ask for). The
``frontend already has a memory panel it can extend later`` — this route is
backend-only for now.

Every field is user-supplied, NEVER invented. The agent's "Who am I?"
intent answers from this store and says "I don't have that information
yet." for anything unknown.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.database import get_session
from app.models import UserProfile

router = APIRouter()

_USER_ID = "local"

# Fields the profile owns. Scalars are replaced/cleared; lists are replaced
# wholesale. Unknown body keys are ignored (never stored).
_SCALAR_FIELDS = ("name", "education", "college", "goals")
_LIST_FIELDS = ("projects", "skills", "preferences")


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None)
    education: str | None = Field(default=None)
    college: str | None = Field(default=None)
    projects: list | None = Field(default=None)
    skills: list | None = Field(default=None)
    goals: str | None = Field(default=None)
    preferences: list | None = Field(default=None)


def _to_dict(row: UserProfile | None) -> dict:
    if row is None:
        return {
            "name": None,
            "education": None,
            "college": None,
            "projects": [],
            "skills": [],
            "goals": None,
            "preferences": [],
        }
    return {
        "name": row.name,
        "education": row.education,
        "college": row.college,
        "projects": row.projects or [],
        "skills": row.skills or [],
        "goals": row.goals,
        "preferences": row.preferences or [],
    }


def _get_or_create(session, user_id: str = _USER_ID) -> UserProfile:
    row = session.get(UserProfile, user_id)
    if row is None:
        row = UserProfile(user_id=user_id)
        session.add(row)
        session.flush()
    return row


def _clean_scalar(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@router.get("/api/profile")
async def get_profile():
    """The stored user profile. Unknown fields are null — never invented."""
    with get_session() as session:
        row = session.get(UserProfile, _USER_ID)
        if row is not None:
            session.expunge(row)
        return _to_dict(row)


@router.put("/api/profile")
async def update_profile(req: ProfileUpdate):
    """Partial update of the user profile.

    Only fields present in the body are touched (Pydantic
    ``exclude_unset``). A scalar set to null clears it; a list replaces the
    stored list. Scalars are stripped; empty strings clear. String entries
    inside lists are stripped and empties dropped; non-string entries are
    kept as-is.
    """
    data = req.model_dump(exclude_unset=True)
    with get_session() as session:
        row = _get_or_create(session)
        for field in _SCALAR_FIELDS:
            if field in data:
                setattr(row, field, _clean_scalar(data[field]))
        for field in _LIST_FIELDS:
            if field in data:
                value = data[field]
                if value is None:
                    setattr(row, field, None)
                else:
                    cleaned = [
                        item.strip() if isinstance(item, str) else item
                        for item in value
                    ]
                    cleaned = [
                        item
                        for item in cleaned
                        if not (isinstance(item, str) and not item)
                    ]
                    setattr(row, field, cleaned)
        session.flush()
        session.expunge(row)
        return _to_dict(row)
