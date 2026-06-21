"""Repository checkout service.

Clones a GitHub repository into a temporary workspace and checks out a specific
commit (typically a pull request head commit), then exposes the local path so
the repository indexer can scan real source files.

The public entry point is :func:`checkout_repository`, a context manager that
always removes its temporary workspace on exit. All git operations run through
``subprocess``; nothing here executes git at import time. Failures are mapped to
a small exception hierarchy so callers can react to clone, checkout, and
missing-repository errors distinctly.
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional, Sequence

logger = logging.getLogger("ai-code-review-worker.checkout")

DEFAULT_GITHUB_HOST = "github.com"
WORKSPACE_PREFIX = "ai-code-review-"
GIT_CLONE_TIMEOUT_SECONDS = 300
GIT_OP_TIMEOUT_SECONDS = 120

_MISSING_REPO_MARKERS = (
    "repository not found",
    "not found",
    "could not read from remote repository",
    "does not exist",
)


class RepositoryCheckoutError(Exception):
    """Base error for any failure during repository checkout."""


class CloneError(RepositoryCheckoutError):
    """Raised when cloning the repository fails."""


class RepositoryNotFoundError(CloneError):
    """Raised when the repository does not exist or is inaccessible."""


class CommitCheckoutError(RepositoryCheckoutError):
    """Raised when checking out the requested commit fails."""


@dataclass
class RepositoryCheckout:
    """A checked-out repository on the local filesystem."""

    path: str
    repository: str
    commit_sha: str


def build_clone_url(
    repository: str, token: Optional[str] = None, host: str = DEFAULT_GITHUB_HOST
) -> str:
    """Build an HTTPS clone URL, embedding a token for private repositories."""
    name = repository.strip().strip("/")
    if name.endswith(".git"):
        name = name[: -len(".git")]
    if token:
        return f"https://x-access-token:{token}@{host}/{name}.git"
    return f"https://{host}/{name}.git"


def _redact(text: Optional[str], token: Optional[str]) -> str:
    cleaned = (text or "").strip()
    if token and token in cleaned:
        cleaned = cleaned.replace(token, "***")
    return cleaned


def _looks_like_missing_repo(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(marker in lowered for marker in _MISSING_REPO_MARKERS)


def _run_git(
    args: Sequence[str],
    *,
    cwd: Optional[str] = None,
    timeout: int = GIT_OP_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Run a git command and return the completed process (never raises on rc)."""
    cmd = ["git", *args]
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:  # git not installed
        raise RepositoryCheckoutError("git executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RepositoryCheckoutError(
            f"git command timed out: git {' '.join(args)}"
        ) from exc


def _clone(repository: str, workspace: str, token: Optional[str]) -> None:
    url = build_clone_url(repository, token)
    completed = _run_git(
        ["clone", "--no-checkout", url, workspace],
        timeout=GIT_CLONE_TIMEOUT_SECONDS,
    )
    if completed.returncode == 0:
        return

    stderr = _redact(completed.stderr, token)
    if _looks_like_missing_repo(stderr):
        raise RepositoryNotFoundError(
            f"repository not found or inaccessible: {repository}: {stderr}"
        )
    raise CloneError(f"failed to clone {repository}: {stderr}")


def _fetch_commit(workspace: str, commit_sha: str, token: Optional[str]) -> None:
    """Best-effort fetch of a specific commit (PR head may not be on a branch)."""
    completed = _run_git(
        ["fetch", "origin", commit_sha],
        cwd=workspace,
        timeout=GIT_CLONE_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        logger.info(
            "checkout_fetch_skipped",
            extra={
                "commit_sha": commit_sha,
                "detail": _redact(completed.stderr, token),
            },
        )


def _checkout_commit(
    workspace: str, repository: str, commit_sha: str, token: Optional[str]
) -> None:
    completed = _run_git(["checkout", "--force", commit_sha], cwd=workspace)
    if completed.returncode != 0:
        stderr = _redact(completed.stderr, token)
        raise CommitCheckoutError(
            f"failed to checkout {commit_sha} for {repository}: {stderr}"
        )


def _on_rm_error(func, path, _exc_info) -> None:
    """rmtree error handler: clear read-only bit (common for .git on Windows)."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _cleanup(workspace: str) -> None:
    """Remove the temporary workspace; never raise on cleanup failure."""
    try:
        shutil.rmtree(workspace, onerror=_on_rm_error)
        logger.info("checkout_cleanup_completed", extra={"workspace": workspace})
    except Exception as exc:  # noqa: BLE001 - cleanup must not break the caller
        logger.warning(
            "checkout_cleanup_failed",
            extra={"workspace": workspace, "error": str(exc)},
        )


@contextmanager
def checkout_repository(
    repository: str,
    commit_sha: str,
    token: Optional[str] = None,
    base_dir: Optional[str] = None,
) -> Iterator[RepositoryCheckout]:
    """Clone ``repository`` and checkout ``commit_sha`` into a temp workspace.

    Yields a :class:`RepositoryCheckout` whose ``path`` points at the
    checked-out tree. The workspace is always deleted on exit, even on error.

    Raises:
        RepositoryCheckoutError: invalid arguments.
        RepositoryNotFoundError: the repository does not exist / is private.
        CloneError: cloning failed for another reason.
        CommitCheckoutError: the commit could not be checked out.
    """
    if not repository:
        raise RepositoryCheckoutError("repository is required")
    if not commit_sha:
        raise RepositoryCheckoutError("commit_sha is required")

    workspace = tempfile.mkdtemp(prefix=WORKSPACE_PREFIX, dir=base_dir)
    logger.info(
        "checkout_started",
        extra={
            "repository": repository,
            "commit_sha": commit_sha,
            "workspace": workspace,
        },
    )
    try:
        _clone(repository, workspace, token)
        _fetch_commit(workspace, commit_sha, token)
        _checkout_commit(workspace, repository, commit_sha, token)
        logger.info(
            "checkout_completed",
            extra={"repository": repository, "commit_sha": commit_sha},
        )
        yield RepositoryCheckout(
            path=workspace, repository=repository, commit_sha=commit_sha
        )
    finally:
        _cleanup(workspace)
