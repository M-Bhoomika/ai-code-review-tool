"""Verify LangGraph is the default orchestration path in deployment configs."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_repo_file(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_env_example_defaults_langgraph_true() -> None:
    content = _read_repo_file(".env.example")
    assert "USE_LANGGRAPH=true" in content


def test_docker_compose_defaults_langgraph_true() -> None:
    content = _read_repo_file("docker-compose.yml")
    assert "USE_LANGGRAPH: ${USE_LANGGRAPH:-true}" in content


def test_e2e_compose_defaults_langgraph_true() -> None:
    content = _read_repo_file("docker-compose.e2e.yml")
    assert 'USE_LANGGRAPH: "true"' in content


def test_k8s_configmap_defaults_langgraph_true() -> None:
    content = _read_repo_file("infrastructure/k8s/configmap.yaml")
    assert 'USE_LANGGRAPH: "true"' in content


def test_helm_values_defaults_langgraph_true() -> None:
    content = _read_repo_file("infrastructure/helm/ai-code-review/values.yaml")
    assert 'useLanggraph: "true"' in content
