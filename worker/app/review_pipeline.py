"""End-to-end review orchestration pipeline.

Wires together diff processing, repository indexing, context retrieval, review
generation, and review publishing. Each stage is a module-level helper so the
flow is easy to test and reason about. GitHub and LLM clients are injected; this
module contains no direct OpenAI, ChromaDB, or GitHub credentials handling.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional, Sequence

from app import (
    context_retriever,
    diff_processor,
    repository_checkout,
    repository_indexer,
    review_engine,
)
from app.context_retriever import DiffContextBundle
from app.diff_processor import ProcessedFile
from app.mlops import mlflow_tracker
from app.monitoring import review_metrics, tracing
from app.review_engine import ReviewComment, ReviewResult

logger = logging.getLogger("ai-code-review-worker.pipeline")

_SEVERITY_DEFAULT = "info"

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _use_langgraph() -> bool:
    """Return True when the LangGraph orchestration layer is enabled.

    Controlled by the ``USE_LANGGRAPH`` environment variable (default on), read
    at call time so it can be toggled per-process/test without re-importing.
    """
    return os.getenv("USE_LANGGRAPH", "true").strip().lower() in _TRUE_VALUES


@dataclass
class PipelineResult:
    repository: str
    pull_number: int
    files_processed: int
    chunks_processed: int
    comments_generated: int
    comments_published: int
    success: bool
    commit_sha: Optional[str] = None
    generated_comments: list[ReviewComment] = field(default_factory=list)
    processing_time_ms: int = 0


# Optional progress callback invoked after key stages with the partial result.
ProgressCallback = Callable[["PipelineResult"], None]


def _emit_progress(
    on_progress: Optional[ProgressCallback], result: "PipelineResult"
) -> None:
    """Invoke the progress callback defensively; never break the pipeline."""
    if on_progress is None:
        return
    try:
        on_progress(result)
    except Exception as exc:  # noqa: BLE001 - progress reporting is best-effort
        logger.warning("progress_callback_failed", extra={"error": str(exc)})


def fetch_pr_files(
    github_client: Any, repository: str, pull_number: int
) -> list[ProcessedFile]:
    """Stage A: fetch and normalize the PR's changed files."""
    logger.info(
        "stage_fetch_pr_files_started",
        extra={"repository": repository, "pull_number": pull_number},
    )
    files = diff_processor.fetch_pr_files(github_client, repository, pull_number)
    logger.info(
        "stage_fetch_pr_files_completed",
        extra={"repository": repository, "files": len(files)},
    )
    return files


def run_diff_processing(
    processed_files: Sequence[ProcessedFile],
) -> list[ProcessedFile]:
    """Stage B: keep only files that produced reviewable diff chunks."""
    logger.info(
        "stage_diff_processing_started",
        extra={"files": len(processed_files)},
    )
    reviewable = [pf for pf in processed_files if pf.chunks]
    chunks = sum(len(pf.chunks) for pf in reviewable)
    logger.info(
        "stage_diff_processing_completed",
        extra={"files": len(reviewable), "chunks": chunks},
    )
    return reviewable


def _resolve_head_sha(
    github_client: Any, repository: str, pull_number: int
) -> Optional[str]:
    """Resolve the PR head commit SHA from the GitHub client (best-effort)."""
    if github_client is None:
        return None
    try:
        repo = github_client.get_repo(repository)
        pull_request = repo.get_pull(pull_number)
        head = getattr(pull_request, "head", None)
        sha = getattr(head, "sha", None)
        return sha or None
    except Exception as exc:  # noqa: BLE001 - resolution is best-effort
        logger.warning(
            "resolve_head_sha_failed",
            extra={"repository": repository, "error": str(exc)},
        )
        return None


@contextmanager
def run_repository_checkout(
    github_client: Any,
    repository: str,
    pull_number: int,
    commit_sha: Optional[str] = None,
    github_token: Optional[str] = None,
) -> Iterator[Optional[str]]:
    """Stage C0: clone the repo and checkout the PR head commit.

    Yields the local checkout path, or ``None`` when checkout is not possible
    (no resolvable commit) or fails. Indexing is best-effort, so a failed
    checkout degrades gracefully rather than failing the whole pipeline. The
    temporary workspace is always cleaned up when the context exits.
    """
    sha = commit_sha or _resolve_head_sha(github_client, repository, pull_number)
    if not sha:
        logger.info(
            "stage_repository_checkout_skipped",
            extra={"repository": repository, "reason": "no commit sha"},
        )
        yield None
        return

    logger.info(
        "stage_repository_checkout_started",
        extra={"repository": repository, "commit_sha": sha},
    )
    try:
        with repository_checkout.checkout_repository(
            repository, sha, token=github_token
        ) as workspace:
            logger.info(
                "stage_repository_checkout_completed",
                extra={"repository": repository, "path": workspace.path},
            )
            yield workspace.path
    except repository_checkout.RepositoryCheckoutError as exc:
        logger.warning(
            "stage_repository_checkout_failed",
            extra={"repository": repository, "error": str(exc)},
        )
        yield None


def run_repository_indexing(
    repository: str, repository_path: Optional[str]
) -> Any:
    """Stage C: index the repository into the vector store (best-effort)."""
    if not repository_path:
        logger.info(
            "stage_repository_indexing_skipped",
            extra={"repository": repository, "reason": "no local path"},
        )
        return None

    logger.info(
        "stage_repository_indexing_started",
        extra={"repository": repository, "path": repository_path},
    )
    result = repository_indexer.index_repository(repository_path, repository)
    logger.info(
        "stage_repository_indexing_completed",
        extra={
            "repository": repository,
            "files_processed": getattr(result, "files_processed", 0),
            "chunks_created": getattr(result, "chunks_created", 0),
        },
    )
    return result


def run_context_retrieval(
    repository: str,
    processed_files: Sequence[ProcessedFile],
    top_k: int = 5,
) -> list[DiffContextBundle]:
    """Stage D: retrieve related context for each diff chunk."""
    logger.info(
        "stage_context_retrieval_started",
        extra={"repository": repository, "files": len(processed_files)},
    )
    bundles = context_retriever.retrieve_context_for_files(
        repository, processed_files, top_k=top_k
    )
    logger.info(
        "stage_context_retrieval_completed",
        extra={"repository": repository, "bundles": len(bundles)},
    )
    return bundles


def run_review_generation(
    repository: str,
    bundles: Sequence[DiffContextBundle],
    llm_client: Any,
) -> ReviewResult:
    """Stage E: generate review comments from the context bundles."""
    logger.info(
        "stage_review_generation_started",
        extra={"repository": repository, "bundles": len(bundles)},
    )
    result = review_engine.generate_reviews_for_bundles(
        repository, bundles, llm_client
    )
    logger.info(
        "stage_review_generation_completed",
        extra={"repository": repository, "comments": result.total_comments},
    )
    return result


def _to_github_payload(comment: ReviewComment) -> dict[str, Any]:
    severity = (comment.severity or _SEVERITY_DEFAULT).strip() or _SEVERITY_DEFAULT
    parts = [f"Severity: {severity}", comment.title, comment.explanation]
    if comment.suggestion:
        parts.append(f"Suggestion:\n{comment.suggestion}")
    return {
        "path": comment.file_path,
        "line": comment.line_number,
        "body": "\n\n".join(p for p in parts if p),
    }


def run_review_publishing(
    github_client: Any,
    repository: str,
    pull_number: int,
    comments: Sequence[ReviewComment],
) -> int:
    """Stage F: publish review comments to GitHub.

    Attempts a single batched review and falls back to publishing comments
    individually. Returns the number of comments published.
    """
    logger.info(
        "stage_review_publishing_started",
        extra={
            "repository": repository,
            "pull_number": pull_number,
            "comments": len(comments),
        },
    )
    if not comments:
        return 0

    repo = github_client.get_repo(repository)
    pull_request = repo.get_pull(pull_number)
    commit_list = list(pull_request.get_commits())
    commit = commit_list[-1] if commit_list else None
    payload = [_to_github_payload(comment) for comment in comments]

    try:
        pull_request.create_review(
            commit=commit,
            body="Automated AI code review",
            event="COMMENT",
            comments=payload,
        )
        logger.info(
            "stage_review_publishing_completed",
            extra={"repository": repository, "published": len(payload)},
        )
        return len(payload)
    except Exception as exc:  # noqa: BLE001 - fall back to individual comments
        logger.warning(
            "batch_publish_failed_falling_back",
            extra={"repository": repository, "error": str(exc)},
        )

    published = 0
    for item in payload:
        try:
            pull_request.create_review_comment(
                body=item["body"],
                commit=commit,
                path=item["path"],
                line=item["line"],
            )
            published += 1
        except Exception as exc:  # noqa: BLE001 - continue on failure
            logger.warning(
                "individual_publish_failed",
                extra={"repository": repository, "error": str(exc)},
            )

    logger.info(
        "stage_review_publishing_completed",
        extra={"repository": repository, "published": published},
    )
    return published


def run_analysis(
    repository: str,
    pull_number: int,
    processed_files: Sequence[ProcessedFile],
    llm_client: Any,
    top_k: int = 5,
) -> ReviewResult:
    """Produce review comments for the PR.

    Dispatches between two interchangeable strategies that return the same
    ``ReviewResult`` shape:

    * LangGraph workflow (``USE_LANGGRAPH=true``): retrieve_context →
      analyze_security/performance/logic → synthesize_review.
    * Default inline pipeline: context retrieval followed by review generation.
    """
    if _use_langgraph():
        # Imported lazily so the default pipeline (and its tests) never require
        # the langgraph dependency to be installed/imported.
        from app import review_graph

        logger.info(
            "analysis_strategy_selected",
            extra={"repository": repository, "strategy": "langgraph"},
        )
        return review_graph.run_review_graph(
            repository, pull_number, processed_files, llm_client, top_k=top_k
        )

    logger.info(
        "analysis_strategy_selected",
        extra={"repository": repository, "strategy": "inline"},
    )
    bundles = run_context_retrieval(repository, processed_files, top_k=top_k)
    if not bundles:
        return ReviewResult(repository=repository, total_comments=0, comments=[])
    return run_review_generation(repository, bundles, llm_client)


def process_pull_request(
    repository: str,
    pull_number: int,
    github_client: Any,
    llm_client: Any,
    repository_path: Optional[str] = None,
    commit_sha: Optional[str] = None,
    github_token: Optional[str] = None,
    top_k: int = 5,
    on_progress: Optional[ProgressCallback] = None,
) -> PipelineResult:
    """Run the full review pipeline for a single pull request."""
    with tracing.span(
        "review.pipeline",
        repository=repository,
        pull_number=pull_number,
    ):
        return _process_pull_request_impl(
            repository=repository,
            pull_number=pull_number,
            github_client=github_client,
            llm_client=llm_client,
            repository_path=repository_path,
            commit_sha=commit_sha,
            github_token=github_token,
            top_k=top_k,
            on_progress=on_progress,
        )


def _process_pull_request_impl(
    repository: str,
    pull_number: int,
    github_client: Any,
    llm_client: Any,
    repository_path: Optional[str] = None,
    commit_sha: Optional[str] = None,
    github_token: Optional[str] = None,
    top_k: int = 5,
    on_progress: Optional[ProgressCallback] = None,
) -> PipelineResult:
    logger.info(
        "pipeline_started",
        extra={"repository": repository, "pull_number": pull_number},
    )
    result = PipelineResult(
        repository=repository,
        pull_number=pull_number,
        files_processed=0,
        chunks_processed=0,
        comments_generated=0,
        comments_published=0,
        success=False,
    )

    start_time = time.perf_counter()
    review_metrics.record_review_started()
    result.commit_sha = commit_sha or _resolve_head_sha(
        github_client, repository, pull_number
    )
    review_result: Optional[ReviewResult] = None

    try:
        # A + B: fetch files and keep reviewable diffs.
        with tracing.span("review.pipeline.diff_processing"):
            processed_files = run_diff_processing(
                fetch_pr_files(github_client, repository, pull_number)
            )
        result.files_processed = len(processed_files)
        result.chunks_processed = sum(len(pf.chunks) for pf in processed_files)
        _emit_progress(on_progress, result)

        if not processed_files:
            result.success = True
        else:
            # C0 + C: checkout the PR head and index the real repository.
            # The checkout context cleans up its temporary workspace on exit.
            with run_repository_checkout(
                github_client,
                repository,
                pull_number,
                commit_sha=commit_sha,
                github_token=github_token,
            ) as checkout_path:
                index_path = checkout_path or repository_path
                with tracing.span("review.pipeline.indexing"):
                    run_repository_indexing(repository, index_path)

                # D + E: analysis (context retrieval + review generation, or the
                # LangGraph workflow when enabled).
                with tracing.span("review.pipeline.analysis"):
                    analysis_result = run_analysis(
                        repository,
                        pull_number,
                        processed_files,
                        llm_client,
                        top_k=top_k,
                    )
                review_result = analysis_result
                result.comments_generated = review_result.total_comments
                result.generated_comments = list(review_result.comments)
                _emit_progress(on_progress, result)

                if not review_result.comments:
                    result.success = True
                else:
                    # F: publish.
                    with tracing.span("review.pipeline.publishing"):
                        result.comments_published = run_review_publishing(
                            github_client,
                            repository,
                            pull_number,
                            review_result.comments,
                        )
                    result.success = True
                    _emit_progress(on_progress, result)
    except Exception as exc:  # noqa: BLE001 - never raise out of the pipeline
        logger.error(
            "pipeline_failed",
            extra={
                "repository": repository,
                "pull_number": pull_number,
                "error": str(exc),
            },
        )
        result.success = False
    finally:
        result.processing_time_ms = int((time.perf_counter() - start_time) * 1000)
        review_metrics.record_review_duration(time.perf_counter() - start_time)
        mlflow_tracker.log_review_run(
            repository=repository,
            pull_number=pull_number,
            llm_client=llm_client,
            pipeline_result=result,
            review_result=review_result,
        )

    if result.success:
        # Comment counts are sourced from stages E and F (generation and
        # publishing), so the published counter is driven by the publishing
        # stage's result without being double-counted.
        review_metrics.record_review_success(
            result.comments_generated, result.comments_published
        )
        logger.info(
            "pipeline_completed",
            extra={
                "repository": repository,
                "pull_number": pull_number,
                "files_processed": result.files_processed,
                "chunks_processed": result.chunks_processed,
                "comments_generated": result.comments_generated,
                "comments_published": result.comments_published,
            },
        )
    else:
        review_metrics.record_review_failure()

    return result
