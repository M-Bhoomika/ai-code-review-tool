"""Tests for the LangGraph multi-step review workflow.

All LLM and vector-store interactions are mocked; no real OpenAI/Chroma calls.
"""
import json
from unittest.mock import MagicMock

import pytest

from app import context_retriever, review_graph, review_pipeline
from app.context_retriever import DiffContextBundle
from app.diff_processor import DiffChunk, DiffLine, ProcessedFile
from app.review_engine import ReviewComment, ReviewResult


def _bundle(file_path="app/main.py", idx=0):
    return DiffContextBundle(
        repository="octocat/hello",
        diff_file_path=file_path,
        chunk_index=idx,
        diff_text="+x = 1",
        retrieved_contexts=[],
    )


def _processed_file(file_path="app/main.py", num_chunks=1):
    line = DiffLine(
        content="x = 1",
        diff_position=1,
        line_type="addition",
        old_line_number=None,
        new_line_number=1,
    )
    return ProcessedFile(
        file_path=file_path,
        additions=num_chunks,
        deletions=0,
        status="modified",
        chunks=[
            DiffChunk(
                file_path=file_path,
                chunk_index=i,
                diff_lines=[line],
                token_estimate=1,
            )
            for i in range(num_chunks)
        ],
    )


def _comment_json(title, severity="high", file_path="app/main.py", line=1):
    return {
        "file_path": file_path,
        "line_number": line,
        "severity": severity,
        "title": title,
        "explanation": "because reasons",
        "suggestion": "do the fix",
    }


def _llm_returning(*titles_per_call):
    """Build a mock LLM whose successive complete() calls return given findings.

    Each argument is a list of comment dicts returned (as JSON) for that call.
    """
    client = MagicMock()
    client.complete.side_effect = [json.dumps(entry) for entry in titles_per_call]
    return client


@pytest.fixture(autouse=True)
def _patch_retrieval(monkeypatch):
    """Default: retrieval returns a single bundle. Tests can override."""
    monkeypatch.setattr(
        context_retriever,
        "retrieve_context_for_files",
        lambda repo, files, top_k=5: [_bundle()],
    )


def test_graph_builds():
    compiled = review_graph.build_review_graph(MagicMock(), top_k=3)
    assert compiled is not None
    # Compiled LangGraph exposes invoke.
    assert hasattr(compiled, "invoke")


def test_state_moves_through_all_nodes():
    # One bundle; each of the 3 analysis nodes makes exactly one LLM call.
    llm = _llm_returning(
        [_comment_json("SQL injection", "critical")],  # security
        [_comment_json("N+1 query", "medium")],  # performance
        [_comment_json("Off by one", "high")],  # logic
    )

    result = review_graph.run_review_graph(
        "octocat/hello", 7, [_processed_file()], llm, top_k=5
    )

    assert isinstance(result, ReviewResult)
    # All three categories contributed a distinct finding.
    titles = {c.title for c in result.comments}
    assert titles == {"SQL injection", "N+1 query", "Off by one"}
    assert result.total_comments == 3


def test_category_findings_are_generated_per_node():
    state = {"context_bundles": [_bundle()], "repository": "octocat/hello"}

    sec = review_graph.analyze_security(
        state, llm_client=_llm_returning([_comment_json("XSS", "high")])
    )
    perf = review_graph.analyze_performance(
        state, llm_client=_llm_returning([_comment_json("Slow loop", "low")])
    )
    logic = review_graph.analyze_logic(
        state, llm_client=_llm_returning([_comment_json("Null deref", "high")])
    )

    assert [c.title for c in sec["security_findings"]] == ["XSS"]
    assert [c.title for c in perf["performance_findings"]] == ["Slow loop"]
    assert [c.title for c in logic["logic_findings"]] == ["Null deref"]


def test_analysis_without_llm_yields_no_findings():
    state = {"context_bundles": [_bundle()], "repository": "octocat/hello"}
    out = review_graph.analyze_security(state, llm_client=None)
    assert out["security_findings"] == []


def test_synthesis_deduplicates_findings():
    # Same file/line/title reported by two categories, different severities.
    dup_low = ReviewComment("a.py", 10, "low", "Race condition", "e", "f")
    dup_high = ReviewComment("a.py", 10, "high", "Race condition", "e", "f")
    unique = ReviewComment("b.py", 3, "medium", "Unused import", "e", "f")

    state = {
        "security_findings": [dup_high],
        "performance_findings": [unique],
        "logic_findings": [dup_low],
        "repository": "octocat/hello",
    }

    out = review_graph.synthesize_review(state)
    final = out["final_comments"]

    titles = sorted(c.title for c in final)
    assert titles == ["Race condition", "Unused import"]
    # The higher-severity duplicate is the one kept.
    race = next(c for c in final if c.title == "Race condition")
    assert race.severity == "high"


def test_deduplicate_preserves_first_seen_order():
    a = ReviewComment("a.py", 1, "high", "A", "e", "f")
    b = ReviewComment("b.py", 2, "high", "B", "e", "f")
    a_again = ReviewComment("a.py", 1, "low", "A", "e", "f")

    deduped = review_graph.deduplicate_findings([a, b, a_again])
    assert [c.title for c in deduped] == ["A", "B"]


def test_empty_retrieval_produces_no_findings(monkeypatch):
    monkeypatch.setattr(
        context_retriever,
        "retrieve_context_for_files",
        lambda repo, files, top_k=5: [],
    )
    llm = MagicMock()

    result = review_graph.run_review_graph(
        "octocat/hello", 7, [_processed_file()], llm, top_k=5
    )

    assert result.total_comments == 0
    # No bundles => no analysis LLM calls.
    llm.complete.assert_not_called()


def test_pipeline_uses_graph_when_flag_enabled(monkeypatch):
    monkeypatch.setenv("USE_LANGGRAPH", "true")

    # Stub the surrounding pipeline stages so we isolate the graph dispatch.
    monkeypatch.setattr(
        review_pipeline,
        "fetch_pr_files",
        MagicMock(return_value=[_processed_file("a.py", 1)]),
    )
    monkeypatch.setattr(
        review_pipeline, "run_repository_indexing", MagicMock(return_value=None)
    )
    import contextlib

    monkeypatch.setattr(
        review_pipeline,
        "run_repository_checkout",
        lambda *a, **k: contextlib.nullcontext(None),
    )
    # These must NOT be used in graph mode.
    inline_retrieval = MagicMock(return_value=[_bundle()])
    inline_generation = MagicMock()
    monkeypatch.setattr(review_pipeline, "run_context_retrieval", inline_retrieval)
    monkeypatch.setattr(review_pipeline, "run_review_generation", inline_generation)

    publish = MagicMock(return_value=1)
    monkeypatch.setattr(review_pipeline, "run_review_publishing", publish)

    graph_comments = [ReviewComment("a.py", 1, "high", "Graph finding", "e", "f")]
    graph_mock = MagicMock(
        return_value=ReviewResult("octocat/hello", 1, graph_comments)
    )
    monkeypatch.setattr(review_graph, "run_review_graph", graph_mock)

    result = review_pipeline.process_pull_request(
        "octocat/hello", 7, MagicMock(), MagicMock()
    )

    assert result.success is True
    assert result.comments_generated == 1
    assert result.comments_published == 1
    graph_mock.assert_called_once()
    # Inline strategy must be bypassed when the graph is enabled.
    inline_retrieval.assert_not_called()
    inline_generation.assert_not_called()
    publish.assert_called_once()
    published_args = publish.call_args.args
    assert published_args[1] == "octocat/hello"
    assert published_args[3] == graph_comments


def test_pipeline_uses_inline_when_flag_disabled(monkeypatch):
    monkeypatch.setenv("USE_LANGGRAPH", "false")

    monkeypatch.setattr(
        review_pipeline,
        "fetch_pr_files",
        MagicMock(return_value=[_processed_file("a.py", 1)]),
    )
    monkeypatch.setattr(
        review_pipeline, "run_repository_indexing", MagicMock(return_value=None)
    )
    import contextlib

    monkeypatch.setattr(
        review_pipeline,
        "run_repository_checkout",
        lambda *a, **k: contextlib.nullcontext(None),
    )
    monkeypatch.setattr(
        review_pipeline,
        "run_context_retrieval",
        MagicMock(return_value=[_bundle()]),
    )
    monkeypatch.setattr(
        review_pipeline,
        "run_review_generation",
        MagicMock(return_value=ReviewResult("octocat/hello", 0, [])),
    )
    graph_mock = MagicMock()
    monkeypatch.setattr(review_graph, "run_review_graph", graph_mock)

    review_pipeline.process_pull_request(
        "octocat/hello", 7, MagicMock(), MagicMock()
    )

    graph_mock.assert_not_called()


def test_pipeline_langgraph_returns_review_result(monkeypatch):
    """Full pipeline with LangGraph enabled returns a populated ReviewResult."""
    monkeypatch.setenv("USE_LANGGRAPH", "true")

    monkeypatch.setattr(
        review_pipeline,
        "fetch_pr_files",
        MagicMock(return_value=[_processed_file("a.py", 1)]),
    )
    monkeypatch.setattr(
        review_pipeline, "run_repository_indexing", MagicMock(return_value=None)
    )
    import contextlib

    monkeypatch.setattr(
        review_pipeline,
        "run_repository_checkout",
        lambda *a, **k: contextlib.nullcontext(None),
    )
    monkeypatch.setattr(
        review_pipeline, "run_review_publishing", MagicMock(return_value=1)
    )

    llm = _llm_returning(
        [_comment_json("SQL injection", "critical")],
        [_comment_json("N+1 query", "medium")],
        [_comment_json("Off by one", "high")],
    )

    result = review_pipeline.process_pull_request(
        "octocat/hello", 7, MagicMock(), llm
    )

    assert result.success is True
    assert result.comments_generated == 3
    assert result.comments_published == 1
    assert len(result.generated_comments) == 3
    assert {comment.title for comment in result.generated_comments} == {
        "SQL injection",
        "N+1 query",
        "Off by one",
    }
