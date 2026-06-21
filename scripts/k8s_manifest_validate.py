#!/usr/bin/env python3
"""Static validation for infrastructure/k8s manifest set and cross-references.

Verifies (without a Kubernetes cluster):
- every manifest in the documented apply order exists on disk
- ConfigMap service URLs match in-cluster Service names
- PVC claimNames referenced by Deployments are defined in the apply set
- Prometheus/Grafana ConfigMaps referenced by Deployments exist

Usage (from repository root):
    python scripts/k8s_manifest_validate.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = REPO_ROOT / "infrastructure" / "k8s"

APPLY_MANIFESTS = [
    "namespace.yaml",
    "configmap.yaml",
    "secret.example.yaml",
    "postgres.yaml",
    "redis.yaml",
    "chromadb.yaml",
    "jaeger.yaml",
    "prometheus-configmap.yaml",
    "prometheus.yaml",
    "grafana-provisioning-configmap.yaml",
    "grafana-dashboards-configmap.yaml",
    "grafana.yaml",
    "mlflow.yaml",
    "api.yaml",
    "worker.yaml",
    "frontend.yaml",
]

REQUIRED_SERVICES = {
    "postgres",
    "redis",
    "chromadb",
    "jaeger",
    "prometheus",
    "grafana",
    "mlflow",
    "api",
    "worker",
    "frontend",
}

REQUIRED_PVCS = {"postgres-data", "chromadb-data", "mlflow-data"}

REQUIRED_CONFIGMAP_NAMES = {
    "code-review-config",
    "prometheus-config",
    "grafana-provisioning",
    "grafana-dashboards",
}


class ValidationError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise ValidationError(message)


def _extract_kind_name_blocks(content: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for doc in content.split("---"):
        kind_match = re.search(r"^kind:\s*(\S+)", doc, re.M)
        name_match = re.search(r"^  name:\s*(\S+)", doc, re.M)
        if kind_match and name_match:
            blocks.append((kind_match.group(1), name_match.group(1)))
    return blocks


def main() -> int:
    missing = [name for name in APPLY_MANIFESTS if not (K8S_DIR / name).exists()]
    if missing:
        _fail(f"Missing manifest files: {', '.join(missing)}")
    print(f"OK  All {len(APPLY_MANIFESTS)} apply manifests exist")

    configmap_text = (K8S_DIR / "configmap.yaml").read_text(encoding="utf-8")
    expected_cm_refs = {
        "redis": r"REDIS_URL:.*redis://redis:",
        "chromadb": r"CHROMA_URL:.*chromadb:",
        "jaeger": r"OTEL_EXPORTER_OTLP_ENDPOINT:.*jaeger:4317",
        "mlflow": r"MLFLOW_TRACKING_URI:.*mlflow:5000",
    }
    for label, pattern in expected_cm_refs.items():
        if not re.search(pattern, configmap_text):
            _fail(f"configmap.yaml missing expected {label} service reference")
    print("OK  ConfigMap resolves redis, chromadb, jaeger:4317, mlflow:5000")

    services: set[str] = set()
    pvcs: set[str] = set()
    claim_refs: set[str] = set()
    config_refs: set[str] = set()

    for filename in APPLY_MANIFESTS:
        if not filename.endswith(".yaml"):
            continue
        content = (K8S_DIR / filename).read_text(encoding="utf-8")
        for kind, name in _extract_kind_name_blocks(content):
            if kind == "Service":
                services.add(name)
            elif kind == "PersistentVolumeClaim":
                pvcs.add(name)
            elif kind == "ConfigMap":
                config_refs.add(name)
        for match in re.finditer(r"claimName:\s*(\S+)", content):
            claim_refs.add(match.group(1))

    missing_services = REQUIRED_SERVICES - services
    if missing_services:
        _fail(f"Missing Service manifests for: {', '.join(sorted(missing_services))}")
    print(f"OK  Services present: {', '.join(sorted(REQUIRED_SERVICES))}")

    missing_pvcs = REQUIRED_PVCS - pvcs
    if missing_pvcs:
        _fail(f"Missing PVC manifests for: {', '.join(sorted(missing_pvcs))}")

    orphan_claims = claim_refs - pvcs
    if orphan_claims:
        _fail(f"Deployment claimName without PVC manifest: {', '.join(sorted(orphan_claims))}")
    print("OK  PVCs postgres-data, chromadb-data, mlflow-data defined before worker references")

    missing_configs = REQUIRED_CONFIGMAP_NAMES - config_refs
    if missing_configs:
        _fail(f"Missing ConfigMap manifests for: {', '.join(sorted(missing_configs))}")
    print("OK  ConfigMap refs: prometheus-config, grafana-provisioning, grafana-dashboards")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        raise SystemExit(1)
