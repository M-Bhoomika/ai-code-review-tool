"""MLflow experiment tracking for review pipeline runs.

Each completed pipeline execution creates an MLflow run with review parameters,
metrics, and artifacts when ``MLFLOW_ENABLED`` is true and a tracking URI is
configured (``MLFLOW_TRACKING_URI``).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.review_engine import ReviewComment, ReviewResult

if TYPE_CHECKING:
    from app.review_pipeline import PipelineResult

logger = logging.getLogger("ai-code-review-worker.mlflow")

_DEFAULT_EXPERIMENT = "ai-code-review"
_TRUE_VALUES = {"1", "true", "yes", "on"}

_SECURITY_KEYWORDS = (
    "security",
    "injection",
    "xss",
    "csrf",
    "auth",
    "credential",
    "secret",
    "vulnerabil",
    "exploit",
    "sanitize",
)
_PERFORMANCE_KEYWORDS = (
    "performance",
    "slow",
    "memory",
    "cpu",
    "cache",
    "latency",
    "optim",
    "throughput",
    "bottleneck",
)
_LOGIC_KEYWORDS = (
    "logic",
    "bug",
    "null",
    "incorrect",
    "wrong",
    "error",
    "edge case",
    "race",
    "off-by-one",
)


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in _TRUE_VALUES


def is_mlflow_enabled() -> bool:
    """Return True when MLflow tracking is enabled and configured."""
    if not _parse_bool(os.getenv("MLFLOW_ENABLED"), default=True):
        return False
    return bool(tracking_uri())


def tracking_uri() -> str:
    """Resolve the MLflow tracking server URI from the environment."""
    return os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000").strip()


def experiment_name() -> str:
    """Resolve the MLflow experiment name from the environment."""
    return os.getenv("MLFLOW_EXPERIMENT_NAME", _DEFAULT_EXPERIMENT).strip()


def resolve_review_mode() -> str:
    """Return the active review orchestration mode."""
    from app.review_pipeline import _use_langgraph

    return "langgraph" if _use_langgraph() else "inline"


def resolve_model_name(llm_client: Any | None) -> str:
    """Resolve the LLM model name used for the review."""
    if llm_client is not None:
        model = getattr(llm_client, "model", None)
        if model:
            return str(model)
    configured = os.getenv("OPENAI_MODEL", "unknown").strip()
    return configured or "unknown"


def classify_comment_category(comment: ReviewComment) -> str | None:
    """Heuristically classify an inline review comment into a finding category."""
    text = " ".join(
        part
        for part in (comment.title, comment.explanation, comment.suggestion)
        if part
    ).lower()
    if any(keyword in text for keyword in _SECURITY_KEYWORDS):
        return "security"
    if any(keyword in text for keyword in _PERFORMANCE_KEYWORDS):
        return "performance"
    if any(keyword in text for keyword in _LOGIC_KEYWORDS):
        return "logic"
    return None


def count_category_findings(comments: list[ReviewComment]) -> dict[str, int]:
    """Count security, performance, and logic findings from inline comments."""
    counts = {"security": 0, "performance": 0, "logic": 0}
    for comment in comments:
        category = classify_comment_category(comment)
        if category:
            counts[category] += 1
    return counts


def resolve_finding_counts(review_result: ReviewResult | None) -> dict[str, int]:
    """Return category finding counts from graph state or inline heuristics."""
    if review_result is None:
        return {"security": 0, "performance": 0, "logic": 0}

    if (
        review_result.security_findings
        or review_result.performance_findings
        or review_result.logic_findings
    ):
        return {
            "security": review_result.security_findings,
            "performance": review_result.performance_findings,
            "logic": review_result.logic_findings,
        }

    return count_category_findings(review_result.comments)


def build_review_summary(
    *,
    repository: str,
    pull_number: int,
    success: bool,
    processing_time_ms: int,
    files_processed: int,
    comments_generated: int,
    comments_published: int,
    finding_counts: dict[str, int],
    review_mode: str,
    model_name: str,
) -> str:
    """Build a human-readable review summary artifact."""
    lines = [
        "Review Summary",
        "==============",
        f"Repository: {repository}",
        f"PR: #{pull_number}",
        f"Success: {success}",
        f"Review mode: {review_mode}",
        f"Model: {model_name}",
        f"Files processed: {files_processed}",
        f"Comments generated: {comments_generated}",
        f"Comments published: {comments_published}",
        f"Processing time (ms): {processing_time_ms}",
        f"Security findings: {finding_counts['security']}",
        f"Performance findings: {finding_counts['performance']}",
        f"Logic findings: {finding_counts['logic']}",
    ]
    return "\n".join(lines) + "\n"


def comments_to_json(comments: list[ReviewComment]) -> str:
    """Serialize generated review comments to JSON for artifact logging."""
    payload = [asdict(comment) for comment in comments]
    return json.dumps(payload, indent=2)


def log_review_run(
    *,
    repository: str,
    pull_number: int,
    llm_client: Any | None,
    pipeline_result: "PipelineResult",
    review_result: ReviewResult | None = None,
) -> None:
    """Create an MLflow run for a completed review pipeline execution."""
    if not is_mlflow_enabled():
        logger.debug("mlflow_tracking_skipped", extra={"reason": "disabled"})
        return

    try:
        import mlflow
    except ImportError:
        logger.warning("mlflow_not_installed")
        return

    review_mode = resolve_review_mode()
    model_name = resolve_model_name(llm_client)
    finding_counts = resolve_finding_counts(review_result)

    try:
        mlflow.set_tracking_uri(tracking_uri())
        mlflow.set_experiment(experiment_name())

        run_name = f"{repository}#{pull_number}"
        with mlflow.start_run(run_name=run_name):
            mlflow.set_tag("repository", repository)
            mlflow.set_tag("pull_number", str(pull_number))
            mlflow.set_tag("success", str(pipeline_result.success).lower())

            mlflow.log_param("repository", repository)
            mlflow.log_param("pr_number", pull_number)
            mlflow.log_param("model_name", model_name)
            mlflow.log_param("review_mode", review_mode)

            mlflow.log_metric("processing_time", pipeline_result.processing_time_ms)
            mlflow.log_metric("total_findings", pipeline_result.comments_generated)
            mlflow.log_metric("security_findings", finding_counts["security"])
            mlflow.log_metric("performance_findings", finding_counts["performance"])
            mlflow.log_metric("logic_findings", finding_counts["logic"])
            mlflow.log_metric("comments_published", pipeline_result.comments_published)

            summary = build_review_summary(
                repository=repository,
                pull_number=pull_number,
                success=pipeline_result.success,
                processing_time_ms=pipeline_result.processing_time_ms,
                files_processed=pipeline_result.files_processed,
                comments_generated=pipeline_result.comments_generated,
                comments_published=pipeline_result.comments_published,
                finding_counts=finding_counts,
                review_mode=review_mode,
                model_name=model_name,
            )
            findings_json = comments_to_json(pipeline_result.generated_comments)

            with tempfile.TemporaryDirectory() as artifact_dir:
                summary_path = Path(artifact_dir) / "review_summary.txt"
                summary_path.write_text(summary, encoding="utf-8")
                findings_path = Path(artifact_dir) / "findings.json"
                findings_path.write_text(findings_json, encoding="utf-8")
                mlflow.log_artifact(str(summary_path))
                mlflow.log_artifact(str(findings_path))

        logger.info(
            "mlflow_run_logged",
            extra={"repository": repository, "pull_number": pull_number},
        )
    except Exception as exc:  # noqa: BLE001 - tracking must not break the pipeline
        logger.warning("mlflow_logging_failed", extra={"error": str(exc)})
