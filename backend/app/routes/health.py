from fastapi import APIRouter

from app.providers import get_provider

router = APIRouter()

_provider = get_provider()


@router.get("/api/health")
async def health():
    return {"status": "ok", "provider": _provider.name}
