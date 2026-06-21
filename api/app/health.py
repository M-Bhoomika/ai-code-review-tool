from fastapi import APIRouter

from app.config import settings

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness/readiness probe for the API service."""
    return {
        "status": "ok",
        "service": settings.service_name,
        "version": settings.version,
    }
