import contextlib
from unittest.mock import MagicMock

import pytest

from app import repository_checkout, review_pipeline
from app.context_retriever import DiffContextBundle
from app.diff_processor import DiffChunk, DiffLine, ProcessedFile
from app.review_engine import ReviewComment, ReviewResult
from app.review_pipeline import PipelineResult, process_pull_request


def _chunk(file_path="app/main.py", idx=0):
    line = DiffLine(
        content="x = 1",
        diff_position=1,
        line_type="addition",
        old_line_number=None,
        new_line_number=1,
    )
    return DiffChunk(
        file_path=file_path,
        chunk_index=idx,
        diff_lines=[line],
        token_estimate=1,
    )


def _processed_file(file_path="app/main.py", num_chunks=1):
    return ProcessedFile(
        file_path=file_path,
        additions=num_chunks,
        deletions=0,
        status="modified",
        chunks=[_chunk(file_path, i) for i in range(num_chunks)],
    )


def _bundle(file_path="app/main.py", idx=0):
    return DiffContextBundle(
        repository="octocat/hello",
        diff_file_path=file_path,
        chunk_index=idx,
        diff_text="+x = 1",
        retrieved_contexts=[],
    )


def _comment(title="Issue"):
    return ReviewComment(
        file_path="app/main.py",
        line_number=1,
        severity="high",
        title=title,
        explanation="explanation",
        suggestion="fix it",
    )


@pytest.fixture
def stub_stages(monkeypatch):
    """Replace each pipeline stage helper with a controllable mock."""
    stubs = {
        "fetch_pr_files": MagicMock(return_value=[]),
        "run_repository_indexing": MagicMock(return_value=None),
        "run_context_retrieval": MagicMock(return_value=[]),
        "run_review_generation": MagicMock(
            return_value=ReviewResult("octocat/hello", 0, [])
        ),
        "run_review_publishing": MagicMock(return_value=0),
    }
    for name, mock in stubs.items():
        monkeypatch.setattr(review_pipeline, name, mock)
    # Checkout is a context manager; stub it to a no-op yielding no local path
    # so the stage tests never touch git.
    monkeypatch.setattr(
        review_pipeline,
        "run_repository_checkout",
        lambda *a, **k: contextlib.nullcontext(None),
    )
    return stubs


def test_successful_pipeline_execution(stub_stages):
    stub_stages["fetch_pr_files"].return_value = [
        _processed_file("a.py", 2),
        _processed_file("b.py", 1),
    ]
    stub_stages["run_context_retrieval"].return_value = [
        _bundle("a.py", 0),
        _bundle("a.py", 1),
        _bundle("b.py", 0),
    ]
    stub_stages["run_review_generation"].return_value = ReviewResult(
        "octocat/hello", 2, [_comment("A"), _comment("B")]
    )
    stub_stages["run_review_publishing"].return_value = 2

    gh, llm = MagicMock(), MagicMock()
    result = process_pull_request("octocat/hello", 7, gh, llm)

    assert isinstance(result, PipelineResult)
    assert result.success is True
    assert result.files_processed == 2
    assert result.chunks_processed == 3
    assert result.comments_generated == 2
    assert result.comments_published == 2


def test_publisher_called_correctly(stub_stages):
    stub_stages["fetch_pr_files"].return_value = [_processed_file("a.py", 1)]
    stub_stages["run_context_retrieval"].return_value = [_bundle("a.py", 0)]
    generated = [_comment("A")]
    stub_stages["run_review_generation"].return_value = ReviewResult(
        "octocat/hello", 1, generated
    )
    stub_stages["run_review_publishing"].return_value = 1

    gh, llm = MagicMock(), MagicMock()
    process_pull_request("octocat/hello", 7, gh, llm)

    stub_stages["run_review_publishing"].assert_called_once_with(
        gh, "octocat/hello", 7, generated
    )


def test_empty_diff_path(stub_stages):
    stub_stages["fetch_pr_files"].return_value = []

    result = process_pull_request("octocat/hello", 7, MagicMock(), MagicMock())

    assert result.success is True
    assert result.files_processed == 0
    assert result.chunks_processed == 0
    stub_stages["run_context_retrieval"].assert_not_called()
    stub_stages["run_review_generation"].assert_not_called()
    stub_stages["run_review_publishing"].assert_not_called()


def test_empty_retrieval_path(stub_stages):
    stub_stages["fetch_pr_files"].return_value = [_processed_file("a.py", 1)]
    stub_stages["run_context_retrieval"].return_value = []

    result = process_pull_request("octocat/hello", 7, MagicMock(), MagicMock())

    assert result.success is True
    assert result.files_processed == 1
    assert result.chunks_processed == 1
    assert result.comments_generated == 0
    stub_stages["run_review_generation"].assert_not_called()
    stub_stages["run_review_publishing"].assert_not_called()


def test_empty_review_path(stub_stages):
    stub_stages["fetch_pr_files"].return_value = [_processed_file("a.py", 1)]
    stub_stages["run_context_retrieval"].return_value = [_bundle("a.py", 0)]
    stub_stages["run_review_generation"].return_value = ReviewResult(
        "octocat/hello", 0, []
    )

    result = process_pull_request("octocat/hello", 7, MagicMock(), MagicMock())

    assert result.success is True
    assert result.comments_generated == 0
    assert result.comments_published == 0
    stub_stages["run_review_publishing"].assert_not_called()


def test_counts_calculated_correctly(stub_stages):
    stub_stages["fetch_pr_files"].return_value = [
        _processed_file("a.py", 3),
        _processed_file("b.py", 2),
    ]
    stub_stages["run_context_retrieval"].return_value = [_bundle() for _ in range(5)]
    stub_stages["run_review_generation"].return_value = ReviewResult(
        "octocat/hello", 4, [_comment(str(i)) for i in range(4)]
    )
    stub_stages["run_review_publishing"].return_value = 3

    result = process_pull_request("octocat/hello", 7, MagicMock(), MagicMock())

    assert result.files_processed == 2
    assert result.chunks_processed == 5
    assert result.comments_generated == 4
    assert result.comments_published == 3


def test_stage_failure_handled_safely(stub_stages):
    stub_stages["fetch_pr_files"].side_effect = RuntimeError("github down")

    result = process_pull_request("octocat/hello", 7, MagicMock(), MagicMock())

    assert isinstance(result, PipelineResult)
    assert result.success is False
    assert result.comments_published == 0


def test_pipeline_result_returned(stub_stages):
    result = process_pull_request("octocat/hello", 7, MagicMock(), MagicMock())
    assert isinstance(result, PipelineResult)
    assert result.repository == "octocat/hello"
    assert result.pull_number == 7


# --- Direct tests for the publishing stage (batch + fallback) ---


def _publishing_client(num_commits=1):
    commit = MagicMock(name="commit")
    pull_request = MagicMock(name="pull_request")
    pull_request.get_commits.return_value = [commit] * num_commits
    repo = MagicMock(name="repo")
    repo.get_pull.return_value = pull_request
    client = MagicMock(name="client")
    client.get_repo.return_value = repo
    return client, pull_request


def test_run_review_publishing_batch_success():
    client, pull_request = _publishing_client()
    published = review_pipeline.run_review_publishing(
        client, "octocat/hello", 7, [_comment("A"), _comment("B")]
    )

    assert published == 2
    pull_request.create_review.assert_called_once()
    kwargs = pull_request.create_review.call_args.kwargs
    assert kwargs["event"] == "COMMENT"
    assert len(kwargs["comments"]) == 2
    assert kwargs["comments"][0]["path"] == "app/main.py"
    pull_request.create_review_comment.assert_not_called()


def test_run_review_publishing_fallback():
    client, pull_request = _publishing_client()
    pull_request.create_review.side_effect = RuntimeError("batch unsupported")

    published = review_pipeline.run_review_publishing(
        client, "octocat/hello", 7, [_comment("A"), _comment("B")]
    )

    assert published == 2
    assert pull_request.create_review_comment.call_count == 2


def test_run_review_publishing_empty():
    client, pull_request = _publishing_client()
    published = review_pipeline.run_review_publishing(
        client, "octocat/hello", 7, []
    )
    assert published == 0
    pull_request.create_review.assert_not_called()


# --- Repository checkout integration with indexing ---


def _github_client_with_head(sha="abc123"):
    head = MagicMock()
    head.sha = sha
    pull_request = MagicMock()
    pull_request.head = head
    repo = MagicMock()
    repo.get_pull.return_value = pull_request
    client = MagicMock()
    client.get_repo.return_value = repo
    return client


def test_resolve_head_sha_from_client():
    client = _github_client_with_head("deadbeef")
    assert (
        review_pipeline._resolve_head_sha(client, "octocat/hello", 7) == "deadbeef"
    )


def test_resolve_head_sha_handles_errors():
    client = MagicMock()
    client.get_repo.side_effect = RuntimeError("boom")
    assert review_pipeline._resolve_head_sha(client, "octocat/hello", 7) is None


def test_run_repository_checkout_yields_path(monkeypatch):
    @contextlib.contextmanager
    def fake_checkout(repository, commit_sha, token=None):
        yield repository_checkout.RepositoryCheckout(
            path="/tmp/work", repository=repository, commit_sha=commit_sha
        )

    monkeypatch.setattr(
        repository_checkout, "checkout_repository", fake_checkout
    )

    client = _github_client_with_head("sha1")
    with review_pipeline.run_repository_checkout(client, "octocat/hello", 7) as path:
        assert path == "/tmp/work"


def test_run_repository_checkout_skips_without_sha(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(
        review_pipeline, "_resolve_head_sha", lambda *a, **k: None
    )
    with review_pipeline.run_repository_checkout(client, "octocat/hello", 7) as path:
        assert path is None


def test_run_repository_checkout_failure_is_best_effort(monkeypatch):
    def boom(*args, **kwargs):
        raise repository_checkout.CloneError("clone failed")

    monkeypatch.setattr(repository_checkout, "checkout_repository", boom)

    with review_pipeline.run_repository_checkout(
        MagicMock(), "octocat/hello", 7, commit_sha="sha1"
    ) as path:
        assert path is None


def test_indexing_uses_checked_out_path(stub_stages, monkeypatch):
    stub_stages["fetch_pr_files"].return_value = [_processed_file("a.py", 1)]
    stub_stages["run_context_retrieval"].return_value = []

    # Replace the no-op checkout stub with one that yields a real local path.
    monkeypatch.setattr(
        review_pipeline,
        "run_repository_checkout",
        lambda *a, **k: contextlib.nullcontext("/tmp/checked-out"),
    )

    process_pull_request("octocat/hello", 7, MagicMock(), MagicMock())

    stub_stages["run_repository_indexing"].assert_called_once_with(
        "octocat/hello", "/tmp/checked-out"
    )
