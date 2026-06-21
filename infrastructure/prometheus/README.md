# Prometheus + Grafana Observability

## Metrics endpoints

| Service | URL | Notes |
|---------|-----|-------|
| API | `http://localhost:8000/metrics` | FastAPI `/metrics` route |
| Worker | `http://localhost:9100/metrics` | Prometheus client HTTP server started on Celery worker init |
| Prometheus | `http://localhost:9090` | Scrapes `api:8000` and `worker:9100` |
| Grafana | `http://localhost:3001` | Admin credentials from `.env` (`admin` / `admin` by default) |

Review lifecycle metrics (`review_jobs_total`, success/failure, comments, duration) are
recorded in the **worker** pipeline. The API `/metrics` endpoint exposes the same metric
definitions for scrape compatibility.

## Grafana dashboard

Provisioned automatically from:

- `infrastructure/grafana/provisioning/dashboards/dashboards.yml`
- `infrastructure/grafana/dashboards/review-metrics.json`

Dashboard title: **AI Code Review Metrics**

Panels:

- Total reviews (`review_jobs_total{job="worker"}`)
- Successful reviews (`review_jobs_success_total{job="worker"}`)
- Failed reviews (`review_jobs_failed_total{job="worker"}`)
- Comments published (`review_comments_published_total{job="worker"}`)
- Review pipeline duration (`review_pipeline_duration_seconds{job="worker"}`)

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `WORKER_METRICS_ENABLED` | `true` | Start worker `/metrics` server |
| `WORKER_METRICS_PORT` | `9100` | Worker metrics port |

## Validation

Direct endpoint + Prometheus scrape check:

```bash
docker compose up --build -d
python scripts/prometheus_validate.py
```

Unit tests:

```bash
cd api && pytest tests/test_review_metrics.py -v
cd ../worker && pytest tests/test_metrics_server.py tests/test_review_metrics.py -v
```

## Kubernetes / Helm

Plain manifests:

- `infrastructure/k8s/prometheus-configmap.yaml`
- `infrastructure/k8s/prometheus.yaml`
- `infrastructure/k8s/grafana-provisioning-configmap.yaml`
- `infrastructure/k8s/grafana-dashboards-configmap.yaml`
- `infrastructure/k8s/grafana.yaml`

Helm chart values: `prometheus.enabled`, `grafana.enabled`, worker metrics port `9100`.
