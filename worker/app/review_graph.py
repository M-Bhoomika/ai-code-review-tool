"""LangGraph multi-step review workflow.

This is an alternative orchestration layer over the *existing* review building
blocks (``context_retriever`` and ``review_engine``). It does not replace the
sequential pipeline in ``review_pipeline`` — that remains the default. When the
``USE_LANGGRAPH`` feature flag is enabled, ``review_pipeline`` invokes this
graph instead of the inline retrieve/generate stages.

Graph shape (fan-out / fan-in)::

                       ┌──> analyze_security ──┐
    retrieve_context ──┼──> analyze_performance┼──> synthesize_review
                       └──> analyze_logic ─────┘

The three analysis nodes are independent and run in the same LangGraph
super-step (parallel where the runtime supports it). Each writes a distinct
state key, so no update conflicts arise; ``errors`` uses an additive reducer so
concurrent error reports are merged rather than dropped.
"""
from __future__ import annotations

import logging
import operator
from functools import partial
from typing import Annotated, Any, Optional, Sequence, TypedDict

from langgraph.graph import END, StateGraph

from app import context_retriever, review_engine
from app.review_engine import LLMClient, ReviewComment, ReviewResult

logger = logging.getLogger("ai-code-review-worker.graph")


class PRReviewState(TypedDict, total=False):
    """Shared state threaded through the review graph."""

    repository: str
    pull_number: int
    processed_files: list[Any]
    context_bundles: list[Any]
    security_findings: list[ReviewComment]
    performance_findings: list[ReviewComment]
    logic_findings: list[ReviewComment]
    final_comments: list[ReviewComment]
    # Written by multiple parallel nodes; merged with list concatenation.
    errors: Annotated[list[str], operator.add]


# Category-specific guidance prepended to the shared review prompt so each node
# focuses the LLM on a single concern.
ANALYSIS_CATEGORIES: dict[str, str] = {
    "security": (
        "You are a security specialist. Focus ONLY on security vulnerabilities: "
        "injection (SQL/command/template), authentication and authorization "
        "flaws, hard-coded secrets, unsafe deserialization, SSRF, path "
        "traversal, and cryptographic misuse. Ignore non-security issues."
    ),
    "performance": (
        "You are a performance specialist. Focus ONLY on performance problems: "
        "N+1 queries, redundant work in loops, unnecessary allocations, "
        "blocking I/O on hot paths, inefficient algorithms or data structures, "
        "and missing caching. Ignore non-performance issues."
    ),
    "logic": (
        "You are a correctness specialist. Focus ONLY on logic and correctness "
        "bugs: off-by-one errors, null/None handling, race conditions, incorrect "
        "conditionals or boolean logic, unhandled edge cases, and faulty error "
        "handling. Ignore stylistic and non-correctness issues."
    ),
}

_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


def build_category_prompt(
    category: str,
    diff_text: str,
    retrieved_contexts: Optional[Sequence[Any]],
) -> str:
    """Build a category-focused prompt by framing the shared review prompt."""
    focus = ANALYSIS_CATEGORIES.get(category, "")
    base_prompt = review_engine.build_review_prompt(diff_text, retrieved_contexts)
    return f"{focus}\n\n{base_prompt}"


def _analyze_category(
    state: PRReviewState, llm_client: Optional[LLMClient], category: str
) -> dict[str, Any]:
    """Run one category of analysis across all context bundles.

    Reuses the existing review engine's prompt construction and response parsing
    so behavior stays consistent with the non-graph pipeline.
    """
    bundles = state.get("context_bundles") or []
    findings: list[ReviewComment] = []
    errors: list[str] = []

    if llm_client is None:
        logger.info(
            "analysis_skipped_no_llm",
            extra={"category": category, "repository": state.get("repository")},
        )
        return {f"{category}_findings": findings}

    for bundle in bundles:
        prompt = build_category_prompt(
            category, bundle.diff_text, bundle.retrieved_contexts
        )
        try:
            response = llm_client.complete(prompt)
        except Exception as exc:  # noqa: BLE001 - degrade per-bundle, keep going
            errors.append(
                f"{category} analysis failed for "
                f"{getattr(bundle, 'diff_file_path', '?')}: {exc}"
            )
            continue
        findings.extend(review_engine.parse_review_response(response))

    logger.info(
        "analysis_completed",
        extra={
            "category": category,
            "repository": state.get("repository"),
            "findings": len(findings),
        },
    )
    out: dict[str, Any] = {f"{category}_findings": findings}
    if errors:
        out["errors"] = errors
    return out


# --- Graph nodes ---------------------------------------------------------------


def retrieve_context(state: PRReviewState, top_k: int = 5) -> dict[str, Any]:
    """Node: retrieve related repository context for the PR's diff chunks."""
    repository = state.get("repository", "")
    processed_files = state.get("processed_files") or []
    logger.info(
        "node_retrieve_context",
        extra={"repository": repository, "files": len(processed_files)},
    )
    try:
        bundles = context_retriever.retrieve_context_for_files(
            repository, processed_files, top_k=top_k
        )
        return {"context_bundles": bundles}
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        logger.warning(
            "node_retrieve_context_failed",
            extra={"repository": repository, "error": str(exc)},
        )
        return {"context_bundles": [], "errors": [f"context retrieval failed: {exc}"]}


def analyze_security(
    state: PRReviewState, llm_client: Optional[LLMClient] = None
) -> dict[str, Any]:
    """Node: identify security findings."""
    return _analyze_category(state, llm_client, "security")


def analyze_performance(
    state: PRReviewState, llm_client: Optional[LLMClient] = None
) -> dict[str, Any]:
    """Node: identify performance findings."""
    return _analyze_category(state, llm_client, "performance")


def analyze_logic(
    state: PRReviewState, llm_client: Optional[LLMClient] = None
) -> dict[str, Any]:
    """Node: identify logic/correctness findings."""
    return _analyze_category(state, llm_client, "logic")


def deduplicate_findings(
    comments: Sequence[ReviewComment],
) -> list[ReviewComment]:
    """Combine findings, removing duplicates.

    Two findings are considered duplicates when they target the same file, line,
    and (case-insensitive) title. When duplicates are found, the one with the
    highest severity is kept; first-seen order is otherwise preserved.
    """
    best: dict[tuple[str, Optional[int], str], ReviewComment] = {}
    order: list[tuple[str, Optional[int], str]] = []

    for comment in comments:
        key = (
            (comment.file_path or "").strip().lower(),
            comment.line_number,
            (comment.title or "").strip().lower(),
        )
        existing = best.get(key)
        if existing is None:
            best[key] = comment
            order.append(key)
        else:
            new_rank = _SEVERITY_RANK.get((comment.severity or "info").lower(), 0)
            old_rank = _SEVERITY_RANK.get((existing.severity or "info").lower(), 0)
            if new_rank > old_rank:
                best[key] = comment

    return [best[key] for key in order]


def synthesize_review(state: PRReviewState) -> dict[str, Any]:
    """Node: combine and deduplicate findings from all analysis nodes."""
    combined: list[ReviewComment] = []
    combined.extend(state.get("security_findings") or [])
    combined.extend(state.get("performance_findings") or [])
    combined.extend(state.get("logic_findings") or [])

    final_comments = deduplicate_findings(combined)
    logger.info(
        "node_synthesize_review",
        extra={
            "repository": state.get("repository"),
            "combined": len(combined),
            "final": len(final_comments),
        },
    )
    return {"final_comments": final_comments}


# --- Graph construction --------------------------------------------------------


def build_review_graph(llm_client: Optional[LLMClient] = None, top_k: int = 5):
    """Build and compile the review graph with clients/config bound to its nodes."""
    graph = StateGraph(PRReviewState)

    graph.add_node("retrieve_context", partial(retrieve_context, top_k=top_k))
    graph.add_node(
        "analyze_security", partial(analyze_security, llm_client=llm_client)
    )
    graph.add_node(
        "analyze_performance",
        partial(analyze_performance, llm_client=llm_client),
    )
    graph.add_node(
        "analyze_logic", partial(analyze_logic, llm_client=llm_client)
    )
    graph.add_node("synthesize_review", synthesize_review)

    graph.set_entry_point("retrieve_context")

    # Fan out to the three independent analysis nodes...
    graph.add_edge("retrieve_context", "analyze_security")
    graph.add_edge("retrieve_context", "analyze_performance")
    graph.add_edge("retrieve_context", "analyze_logic")

    # ...then fan in to synthesis once all analyses complete.
    graph.add_edge("analyze_security", "synthesize_review")
    graph.add_edge("analyze_performance", "synthesize_review")
    graph.add_edge("analyze_logic", "synthesize_review")

    graph.add_edge("synthesize_review", END)

    return graph.compile()


def run_review_graph(
    repository: str,
    pull_number: int,
    processed_files: Sequence[Any],
    llm_client: Optional[LLMClient],
    top_k: int = 5,
) -> ReviewResult:
    """Execute the review graph and return an aggregated :class:`ReviewResult`.

    This is the entry point used by ``review_pipeline`` when ``USE_LANGGRAPH`` is
    enabled. The return type matches ``review_engine.generate_reviews_for_bundles``
    so the pipeline can treat both paths identically.
    """
    compiled = build_review_graph(llm_client, top_k=top_k)
    initial_state: PRReviewState = {
        "repository": repository,
        "pull_number": pull_number,
        "processed_files": list(processed_files),
        "context_bundles": [],
        "security_findings": [],
        "performance_findings": [],
        "logic_findings": [],
        "final_comments": [],
        "errors": [],
    }

    final_state = compiled.invoke(initial_state)
    comments = final_state.get("final_comments") or []

    if final_state.get("errors"):
        logger.info(
            "graph_completed_with_errors",
            extra={
                "repository": repository,
                "errors": len(final_state["errors"]),
            },
        )

    logger.info(
        "graph_review_completed",
        extra={"repository": repository, "comments": len(comments)},
    )
    return ReviewResult(
        repository=repository,
        total_comments=len(comments),
        comments=list(comments),
        security_findings=len(final_state.get("security_findings") or []),
        performance_findings=len(final_state.get("performance_findings") or []),
        logic_findings=len(final_state.get("logic_findings") or []),
    )
