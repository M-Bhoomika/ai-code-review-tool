from types import SimpleNamespace
from unittest.mock import MagicMock

from app.github.review_publisher import (
    GitHubReviewComment,
    PublishResult,
    build_review_comment,
    publish_batch_review,
    publish_review_comments,
)


def _comment(
    file_path="app/main.py",
    line_number=12,
    severity="high",
    title="Possible NPE",
    explanation="value may be None here.",
    suggestion="Add a null check.",
):
    return SimpleNamespace(
        file_path=file_path,
        line_number=line_number,
        severity=severity,
        title=title,
        explanation=explanation,
        suggestion=suggestion,
    )


def _make_client(num_commits=1):
    commit = MagicMock(name="commit")
    pull_request = MagicMock(name="pull_request")
    pull_request.get_commits.return_value = [commit] * num_commits
    repo = MagicMock(name="repo")
    repo.get_pull.return_value = pull_request
    client = MagicMock(name="client")
    client.get_repo.return_value = repo
    return client, repo, pull_request


def test_build_review_comment_body_formatting():
    gh = build_review_comment(_comment())

    assert isinstance(gh, GitHubReviewComment)
    assert gh.path == "app/main.py"
    assert gh.line == 12
    assert gh.body == (
        "Severity: high\n\n"
        "Possible NPE\n\n"
        "value may be None here.\n\n"
        "Suggestion:\nAdd a null check."
    )


def test_build_review_comment_without_suggestion():
    gh = build_review_comment(_comment(suggestion=""))
    assert "Suggestion:" not in gh.body
    assert gh.body == (
        "Severity: high\n\nPossible NPE\n\nvalue may be None here."
    )


def test_comment_conversion_maps_fields():
    gh = build_review_comment(
        _comment(file_path="src/x.py", line_number=None, severity="low")
    )
    assert gh.path == "src/x.py"
    assert gh.line is None
    assert gh.body.startswith("Severity: low")


def test_successful_publish_counts():
    client, _repo, pull_request = _make_client()
    comments = [_comment(title="A"), _comment(title="B")]

    result = publish_review_comments(client, "octocat/hello", 7, comments)

    assert isinstance(result, PublishResult)
    assert result.comments_attempted == 2
    assert result.comments_created == 2
    assert result.comments_failed == 0
    assert pull_request.create_review_comment.call_count == 2


def test_partial_failures_counts():
    client, _repo, pull_request = _make_client()
    pull_request.create_review_comment.side_effect = [
        None,
        RuntimeError("rate limited"),
        None,
    ]
    comments = [_comment(title="A"), _comment(title="B"), _comment(title="C")]

    result = publish_review_comments(client, "octocat/hello", 7, comments)

    assert result.comments_attempted == 3
    assert result.comments_created == 2
    assert result.comments_failed == 1


def test_publish_empty_returns_zero_counts():
    client, _repo, pull_request = _make_client()
    result = publish_review_comments(client, "octocat/hello", 7, [])

    assert result == PublishResult(0, 0, 0)
    pull_request.create_review_comment.assert_not_called()


def test_publish_handles_pull_request_load_failure():
    client = MagicMock()
    client.get_repo.side_effect = RuntimeError("not found")

    result = publish_review_comments(
        client, "octocat/hello", 7, [_comment(), _comment()]
    )

    assert result.comments_attempted == 2
    assert result.comments_created == 0
    assert result.comments_failed == 2


def test_batch_success_counts():
    client, _repo, pull_request = _make_client()
    comments = [_comment(title="A"), _comment(title="B")]

    result = publish_batch_review(client, "octocat/hello", 7, comments)

    assert result.comments_attempted == 2
    assert result.comments_created == 2
    assert result.comments_failed == 0

    pull_request.create_review.assert_called_once()
    kwargs = pull_request.create_review.call_args.kwargs
    assert kwargs["event"] == "COMMENT"
    assert len(kwargs["comments"]) == 2
    assert kwargs["comments"][0]["path"] == "app/main.py"
    assert kwargs["comments"][0]["line"] == 12
    # Individual path not used on batch success.
    pull_request.create_review_comment.assert_not_called()


def test_batch_fallback_path():
    client, _repo, pull_request = _make_client()
    pull_request.create_review.side_effect = RuntimeError("batch unsupported")
    comments = [_comment(title="A"), _comment(title="B")]

    result = publish_batch_review(client, "octocat/hello", 7, comments)

    # Fell back to individual publishing.
    assert pull_request.create_review.call_count == 1
    assert pull_request.create_review_comment.call_count == 2
    assert result.comments_attempted == 2
    assert result.comments_created == 2
    assert result.comments_failed == 0


def test_batch_empty_returns_zero_counts():
    client, _repo, pull_request = _make_client()
    result = publish_batch_review(client, "octocat/hello", 7, [])

    assert result == PublishResult(0, 0, 0)
    pull_request.create_review.assert_not_called()
