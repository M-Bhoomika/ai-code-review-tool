import json
from unittest.mock import MagicMock

from app.context_retriever import DiffContextBundle, RetrievedContext
from app.review_engine import (
    REVIEW_CATEGORIES,
    ReviewComment,
    ReviewResult,
    build_review_prompt,
    generate_review,
    generate_reviews_for_bundles,
    parse_review_response,
)


def _context(content="CONTEXT_SNIPPET_MARKER"):
    return RetrievedContext(
        repository="octocat/hello",
        file_path="app/auth.py",
        language="python",
        start_line=1,
        end_line=20,
        content=content,
        score=0.1,
    )


def _bundle(diff_text="DIFF_MARKER", contexts=None, file_path="app/main.py", idx=0):
    return DiffContextBundle(
        repository="octocat/hello",
        diff_file_path=file_path,
        chunk_index=idx,
        diff_text=diff_text,
        retrieved_contexts=contexts if contexts is not None else [_context()],
    )


def _valid_comment(title="Issue", file_path="app/main.py"):
    return {
        "file_path": file_path,
        "line_number": 12,
        "severity": "high",
        "title": title,
        "explanation": "This can break things.",
        "suggestion": "Fix it like this.",
    }


def test_prompt_contains_diff_and_context():
    prompt = build_review_prompt("DIFF_MARKER", [_context("CONTEXT_SNIPPET_MARKER")])

    assert "DIFF_MARKER" in prompt
    assert "CONTEXT_SNIPPET_MARKER" in prompt
    assert "app/auth.py" in prompt
    for category in REVIEW_CATEGORIES:
        assert category in prompt


def test_prompt_handles_no_context():
    prompt = build_review_prompt("DIFF_MARKER", [])
    assert "DIFF_MARKER" in prompt
    assert "(no related context found)" in prompt


def test_parser_extracts_comments_correctly():
    payload = json.dumps([_valid_comment(title="A"), _valid_comment(title="B")])
    comments = parse_review_response(payload)

    assert len(comments) == 2
    assert isinstance(comments[0], ReviewComment)
    assert comments[0].title == "A"
    assert comments[0].line_number == 12
    assert comments[0].severity == "high"
    assert comments[0].suggestion == "Fix it like this."


def test_parser_handles_fenced_json():
    payload = "```json\n" + json.dumps([_valid_comment()]) + "\n```"
    comments = parse_review_response(payload)
    assert len(comments) == 1


def test_parser_handles_comments_wrapper_object():
    payload = json.dumps({"comments": [_valid_comment()]})
    comments = parse_review_response(payload)
    assert len(comments) == 1


def test_parser_malformed_output_handled_safely():
    assert parse_review_response("this is not json") == []
    assert parse_review_response("") == []
    assert parse_review_response("   ") == []
    assert parse_review_response("{not: valid}") == []


def test_parser_ignores_incomplete_entries():
    entries = [
        _valid_comment(title="Complete"),
        {"file_path": "x.py", "severity": "low"},  # missing title/explanation
        {"title": "no file", "explanation": "x"},  # missing file_path
        "not-an-object",
    ]
    comments = parse_review_response(json.dumps(entries))

    assert len(comments) == 1
    assert comments[0].title == "Complete"


def test_parser_coerces_invalid_line_number_to_none():
    entry = _valid_comment()
    entry["line_number"] = "not-a-number"
    comments = parse_review_response(json.dumps([entry]))
    assert comments[0].line_number is None


def test_generate_review_calls_llm_once_and_parses():
    llm_client = MagicMock()
    llm_client.complete.return_value = json.dumps([_valid_comment()])

    bundle = _bundle(diff_text="UNIQUE_DIFF")
    result = generate_review("octocat/hello", bundle, llm_client)

    assert llm_client.complete.call_count == 1
    sent_prompt = llm_client.complete.call_args.args[0]
    assert "UNIQUE_DIFF" in sent_prompt

    assert isinstance(result, ReviewResult)
    assert result.repository == "octocat/hello"
    assert result.total_comments == 1
    assert len(result.comments) == 1


def test_generate_review_handles_llm_failure():
    llm_client = MagicMock()
    llm_client.complete.side_effect = RuntimeError("model down")

    result = generate_review("octocat/hello", _bundle(), llm_client)

    assert result.total_comments == 0
    assert result.comments == []


def test_generate_reviews_for_bundles_aggregates():
    llm_client = MagicMock()
    llm_client.complete.return_value = json.dumps(
        [_valid_comment(title="A"), _valid_comment(title="B")]
    )

    bundles = [
        _bundle(file_path="a.py", idx=0),
        _bundle(file_path="b.py", idx=1),
        _bundle(file_path="c.py", idx=2),
    ]
    result = generate_reviews_for_bundles("octocat/hello", bundles, llm_client)

    # One LLM call per bundle.
    assert llm_client.complete.call_count == 3
    # 2 comments per bundle * 3 bundles = 6 aggregated.
    assert result.total_comments == 6
    assert len(result.comments) == 6
    assert {c.title for c in result.comments} == {"A", "B"}


def test_generate_reviews_for_bundles_empty():
    llm_client = MagicMock()
    result = generate_reviews_for_bundles("octocat/hello", [], llm_client)

    assert result.total_comments == 0
    assert result.comments == []
    llm_client.complete.assert_not_called()
