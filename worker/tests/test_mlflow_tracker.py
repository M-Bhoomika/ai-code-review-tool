"""Unit tests for MLflow review tracking."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from app.mlops import mlflow_tracker
from app.review_engine import ReviewComment, ReviewResult


@pytest.fixture(autouse=True)
def reset_mlflow_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
    monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "ai-code-review-test")
    monkeypatch.setenv("USE_LANGGRAPH", "false")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")


def _sample_comment() -> ReviewComment:
    return ReviewComment(
        file_path="app/main.py",
        line_number=2,
        severity="high",
        title="Possible null return",
        explanation="The function may return None unexpectedly.",
        suggestion="Return a concrete default value.",
    )


def test_is_mlflow_enabled_respects_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MLFLOW_ENABLED", "false")
    assert mlflow_tracker.is_mlflow_enabled() is False


def test_classify_comment_category_logic() -> None:
    assert mlflow_tracker.classify_comment_category(_sample_comment()) == "logic"


def test_classify_comment_category_security() -> None:
    comment = ReviewComment(
        file_path="app/auth.py",
        line_number=1,
        severity="critical",
        title="SQL injection risk",
        explanation="User input is concatenated into a query.",
        suggestion="Use parameterized queries.",
    )
    assert mlflow_tracker.classify_comment_category(comment) == "security"


def test_resolve_finding_counts_prefers_graph_counts() -> None:
    review_result = ReviewResult(
        repository="octocat/hello",
        total_comments=1,
        comments=[_sample_comment()],
        security_findings=2,
        performance_findings=3,
        logic_findings=4,
    )
    counts = mlflow_tracker.resolve_finding_counts(review_result)
    assert counts == {"security": 2, "performance": 3, "logic": 4}


def test_resolve_finding_counts_classifies_inline_comments() -> None:
    review_result = ReviewResult(
        repository="octocat/hello",
        total_comments=1,
        comments=[_sample_comment()],
    )
    counts = mlflow_tracker.resolve_finding_counts(review_result)
    assert counts["logic"] == 1


def test_resolve_model_name_from_client() -> None:
    client = SimpleNamespace(model="custom-model")
    assert mlflow_tracker.resolve_model_name(client) == "custom-model"


@patch("mlflow.log_artifact")
@patch("mlflow.log_metric")
@patch("mlflow.log_param")
@patch("mlflow.set_tag")
@patch("mlflow.start_run")
@patch("mlflow.set_experiment")
@patch("mlflow.set_tracking_uri")
def test_log_review_run_records_params_metrics_and_artifacts(
    mock_set_tracking_uri: MagicMock,
    mock_set_experiment: MagicMock,
    mock_start_run: MagicMock,
    mock_set_tag: MagicMock,
    mock_log_param: MagicMock,
    mock_log_metric: MagicMock,
    mock_log_artifact: MagicMock,
) -> None:
    mock_run = MagicMock()
    mock_start_run.return_value.__enter__.return_value = mock_run

    pipeline_result = SimpleNamespace(
        repository="octocat/hello",
        pull_number=42,
        success=True,
        processing_time_ms=1500,
        files_processed=1,
        comments_generated=1,
        comments_published=1,
        generated_comments=[_sample_comment()],
    )
    review_result = ReviewResult(
        repository="octocat/hello",
        total_comments=1,
        comments=[_sample_comment()],
    )

    mlflow_tracker.log_review_run(
        repository="octocat/hello",
        pull_number=42,
        llm_client=SimpleNamespace(model="e2e-stub"),
        pipeline_result=pipeline_result,
        review_result=review_result,
    )

    mock_set_tracking_uri.assert_called_once_with("http://mlflow:5000")
    mock_set_experiment.assert_called_once_with("ai-code-review-test")
    mock_start_run.assert_called_once_with(run_name="octocat/hello#42")

    logged_params = {
        call.args[0]: call.args[1] for call in mock_log_param.call_args_list
    }
    assert logged_params["repository"] == "octocat/hello"
    assert logged_params["pr_number"] == 42
    assert logged_params["model_name"] == "e2e-stub"
    assert logged_params["review_mode"] == "inline"

    logged_metrics = {
        call.args[0]: call.args[1] for call in mock_log_metric.call_args_list
    }
    assert logged_metrics["processing_time"] == 1500
    assert logged_metrics["total_findings"] == 1
    assert logged_metrics["logic_findings"] == 1
    assert logged_metrics["comments_published"] == 1

    artifact_paths = [Path(call.args[0]).name for call in mock_log_artifact.call_args_list]
    assert "review_summary.txt" in artifact_paths
    assert "findings.json" in artifact_paths


@patch("mlflow.start_run")
def test_log_review_run_skips_when_disabled(
    mock_start_run: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MLFLOW_ENABLED", "false")
    pipeline_result = SimpleNamespace(
        repository="octocat/hello",
        pull_number=42,
        success=True,
        processing_time_ms=100,
        files_processed=0,
        comments_generated=0,
        comments_published=0,
        generated_comments=[],
    )

    mlflow_tracker.log_review_run(
        repository="octocat/hello",
        pull_number=42,
        llm_client=None,
        pipeline_result=pipeline_result,
    )

    mock_start_run.assert_not_called()
