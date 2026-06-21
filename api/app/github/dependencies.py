from github import Github

from app.github.auth import get_github_client


async def get_github_installation_client(installation_id: int) -> Github:
    """FastAPI dependency yielding a GitHub client for an installation.

    Usage:
        @router.post("/repos/{installation_id}/sync")
        async def sync(
            client: Github = Depends(get_github_installation_client),
        ):
            ...
    """
    return await get_github_client(installation_id)
