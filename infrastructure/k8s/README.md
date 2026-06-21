# Kubernetes Deployment

Manifests to run the AI Code Review Tool on Kubernetes. They mirror the
service topology in `docker-compose.yml` using in-cluster DNS names.

## Layout

| File | Resources |
| ---- | --------- |
| `namespace.yaml` | Namespace `code-review` |
| `configmap.yaml` | Non-secret configuration (URLs, feature flags) |
| `secret.example.yaml` | Example Secret — copy and edit before apply |
| `postgres.yaml` | PVC, Service, Deployment |
| `redis.yaml` | Service, Deployment |
| `chromadb.yaml` | PVC, Service, Deployment |
| `api.yaml` | Service, Deployment |
| `worker.yaml` | Service, Deployment |
| `frontend.yaml` | Service, Deployment |
| `jaeger.yaml` | Jaeger all-in-one (OTLP gRPC + UI) |
| `prometheus-configmap.yaml` | Prometheus scrape configuration |
| `prometheus.yaml` | Prometheus Deployment + Service |
| `grafana-provisioning-configmap.yaml` | Grafana datasource + dashboard provider |
| `grafana-dashboards-configmap.yaml` | Review metrics dashboard JSON |
| `grafana.yaml` | Grafana Deployment + Service |
| `mlflow.yaml` | MLflow PVC, Service, Deployment (experiment tracking) |

All workloads run in the `code-review` namespace. The apply order below
includes every manifest referenced by ConfigMap URLs, Prometheus scrape
targets, and worker volume mounts (including the shared `mlflow-data` PVC).

## Prerequisites

- Kubernetes cluster (local: minikube, kind, Docker Desktop Kubernetes, etc.)
- `kubectl` configured for your cluster
- Container images built locally (or pushed to a registry and image names updated)

## Build images

From the repository root:

```bash
docker build -t code-review-api:latest ./api
docker build -t code-review-worker:latest ./worker
docker build -t code-review-frontend:latest ./frontend
```

If using minikube/kind, load images into the cluster:

```bash
# minikube
minikube image load code-review-api:latest
minikube image load code-review-worker:latest
minikube image load code-review-frontend:latest

# kind
kind load docker-image code-review-api:latest
kind load docker-image code-review-worker:latest
kind load docker-image code-review-frontend:latest
```

### Frontend API URL

The dashboard defaults to `http://localhost:8000` in `frontend/src/lib/api.ts`,
which matches the port-forward commands below. To point the dashboard at a
different public API URL, rebuild the frontend image with that value baked in
(extend `frontend/Dockerfile` with a `NEXT_PUBLIC_API_URL` build ARG if needed).

## Configure secrets

```bash
cd infrastructure/k8s
cp secret.example.yaml secret.yaml
# Edit secret.yaml — set POSTGRES_PASSWORD, DATABASE_URL (same password), and
# optional GITHUB_TOKEN, OPENAI_API_KEY, GITHUB_WEBHOOK_SECRET, etc.
```

Ensure `DATABASE_URL` uses the in-cluster postgres hostname:

```text
postgresql://postgres:<password>@postgres:5432/code_review
```

Do not commit `secret.yaml`.

## Apply manifests

Apply in dependency order. Observability and MLflow must be applied **before**
`worker.yaml` — the worker mounts the `mlflow-data` PVC (created in
`mlflow.yaml`) and reads `OTEL_EXPORTER_OTLP_ENDPOINT` / `MLFLOW_TRACKING_URI`
from the ConfigMap (`jaeger:4317`, `mlflow:5000`).

```bash
cd infrastructure/k8s

# Core platform
kubectl apply -f namespace.yaml
kubectl apply -f configmap.yaml
kubectl apply -f secret.yaml
kubectl apply -f postgres.yaml
kubectl apply -f redis.yaml
kubectl apply -f chromadb.yaml

# Observability + MLflow (ConfigMap URLs and worker PVC dependencies)
kubectl apply -f jaeger.yaml
kubectl apply -f prometheus-configmap.yaml
kubectl apply -f prometheus.yaml
kubectl apply -f grafana-provisioning-configmap.yaml
kubectl apply -f grafana-dashboards-configmap.yaml
kubectl apply -f grafana.yaml
kubectl apply -f mlflow.yaml

# Application tier (api migrations must finish before worker starts)
kubectl apply -f api.yaml
kubectl apply -f worker.yaml
kubectl apply -f frontend.yaml
```

Or apply the full stack in one step after creating `secret.yaml`:

```bash
cd infrastructure/k8s

kubectl apply \
  -f namespace.yaml \
  -f configmap.yaml \
  -f secret.yaml \
  -f postgres.yaml \
  -f redis.yaml \
  -f chromadb.yaml \
  -f jaeger.yaml \
  -f prometheus-configmap.yaml \
  -f prometheus.yaml \
  -f grafana-provisioning-configmap.yaml \
  -f grafana-dashboards-configmap.yaml \
  -f grafana.yaml \
  -f mlflow.yaml \
  -f api.yaml \
  -f worker.yaml \
  -f frontend.yaml
```

## Verify deployment

```bash
kubectl -n code-review get pods
kubectl -n code-review get svc
kubectl -n code-review get pvc
```

Wait until all pods are `Running`. The api Deployment runs Alembic migrations on
startup (`api/entrypoint.sh`); the worker init container blocks until
`http://api:8000/health` succeeds.

Confirm observability and MLflow services exist (names must match ConfigMap
URLs):

```bash
kubectl -n code-review get svc jaeger mlflow prometheus grafana
kubectl -n code-review get pvc mlflow-data
```

Dry-run validation (no cluster changes; use your real `secret.yaml` locally):

```bash
cd infrastructure/k8s

kubectl apply --dry-run=client --validate=false \
  -f namespace.yaml \
  -f configmap.yaml \
  -f secret.example.yaml \
  -f postgres.yaml \
  -f redis.yaml \
  -f chromadb.yaml \
  -f jaeger.yaml \
  -f prometheus-configmap.yaml \
  -f prometheus.yaml \
  -f grafana-provisioning-configmap.yaml \
  -f grafana-dashboards-configmap.yaml \
  -f grafana.yaml \
  -f mlflow.yaml \
  -f api.yaml \
  -f worker.yaml \
  -f frontend.yaml
```

From the repository root, you can also run a static manifest graph check (no
cluster required):

```bash
python scripts/k8s_manifest_validate.py
```

## Access locally (port-forward)

Application:

```bash
kubectl -n code-review port-forward svc/api 8000:8000
kubectl -n code-review port-forward svc/frontend 3000:3000
```

Observability (optional; run in separate terminals):

```bash
kubectl -n code-review port-forward svc/jaeger 16686:16686
kubectl -n code-review port-forward svc/prometheus 9090:9090
kubectl -n code-review port-forward svc/grafana 3001:3000
kubectl -n code-review port-forward svc/mlflow 5000:5000
```

- API: http://localhost:8000/health
- Dashboard: http://localhost:3000/dashboard
- Jaeger UI: http://localhost:16686
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3001 (admin / admin)
- MLflow: http://localhost:5000

## Environment variables

### ConfigMap (`code-review-config`)

| Key | Used by | Purpose |
| --- | ------- | ------- |
| `POSTGRES_USER` | postgres | Database user |
| `POSTGRES_DB` | postgres | Database name |
| `REDIS_URL` | api, worker | Celery broker/backend |
| `CHROMA_URL` | api, worker | Vector store HTTP URL |
| `USE_GITHUB_APP_AUTH` | worker | App vs PAT auth |
| `USE_LANGGRAPH` | worker | LangGraph workflow flag |
| `OPENAI_MODEL` | worker | LLM model name |
| `GITHUB_API_URL` | api, worker | GitHub API base URL |
| `GITHUB_APP_ID` | api | GitHub App ID (non-secret) |
| `OTEL_ENABLED` | api, worker | Enable OpenTelemetry export |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | api, worker | Jaeger OTLP gRPC (`http://jaeger:4317`) |
| `OTEL_EXPORTER_OTLP_INSECURE` | api, worker | In-cluster OTLP without TLS |
| `OTEL_SERVICE_NAME_API` / `OTEL_SERVICE_NAME_WORKER` | api, worker | Trace service names |
| `WORKER_METRICS_ENABLED` | worker | Expose Prometheus metrics on `:9100` |
| `WORKER_METRICS_PORT` | worker | Metrics port (scraped by Prometheus) |
| `MLFLOW_ENABLED` | worker | Log review runs to MLflow |
| `MLFLOW_TRACKING_URI` | worker | MLflow server (`http://mlflow:5000`) |
| `MLFLOW_EXPERIMENT_NAME` | worker | MLflow experiment name |
| `NEXT_PUBLIC_API_URL` | docs / build | Browser API base URL |

### Secret (`code-review-secrets`)

| Key | Used by | Purpose |
| --- | ------- | ------- |
| `POSTGRES_PASSWORD` | postgres | Database password |
| `DATABASE_URL` | api, worker | SQLAlchemy connection string |
| `GITHUB_WEBHOOK_SECRET` | api | Webhook HMAC verification |
| `GITHUB_TOKEN` | worker | PAT authentication |
| `GITHUB_PRIVATE_KEY` | worker | GitHub App private key (PEM) |
| `OPENAI_API_KEY` | worker | LLM provider key |
| `OPENAI_BASE_URL` | worker | Optional OpenAI-compatible URL |

See `.env.example` at the repository root for the same variables used by
Docker Compose.

## Startup order

Init containers enforce the same ordering as `docker-compose.yml`:

1. **postgres**, **redis**, **chromadb** — data services (PVCs: `postgres-data`, `chromadb-data`)
2. **jaeger**, **prometheus**, **grafana**, **mlflow** — observability + MLflow (`mlflow-data` PVC)
3. **api** — waits for postgres/redis; runs Alembic migrations on start
4. **worker** — waits for postgres, redis, and api `/health`; requires `mlflow-data` PVC and reaches `jaeger` / `mlflow` via ConfigMap URLs
5. **frontend** — waits for api `/health`

Prometheus scrapes `api:8000/metrics` and `worker:9100/metrics` once those pods
are running (see `prometheus-configmap.yaml`).

## Production notes

- Replace `image: code-review-*:latest` with your registry paths and pin tags.
- Use a managed PostgreSQL/Redis service instead of in-cluster Deployments when
  appropriate; update `DATABASE_URL` / `REDIS_URL` in ConfigMap/Secret.
- Add Ingress resources for external HTTPS (not included here).
- Tune CPU/memory requests and PVC sizes for your workload.
- For Helm-based installs with parameterized values, see [`../helm/README.md`](../helm/README.md).

## Troubleshooting

| Symptom | Check |
| ------- | ----- |
| api CrashLoop | `kubectl -n code-review logs deploy/api` — migration or DB URL errors |
| worker Pending | `kubectl -n code-review describe pod -l app.kubernetes.io/name=worker` — apply `mlflow.yaml` first (`mlflow-data` PVC) |
| worker idle | `GITHUB_TOKEN` / `OPENAI_API_KEY` in Secret; Redis reachable |
| no traces in Jaeger | `kubectl -n code-review get svc jaeger`; ConfigMap `OTEL_EXPORTER_OTLP_ENDPOINT` |
| MLflow unreachable | `kubectl -n code-review get svc mlflow`; apply `mlflow.yaml` before `worker.yaml` |
| dashboard API errors | Frontend built with correct `NEXT_PUBLIC_API_URL`; port-forward api |
| postgres / mlflow pending PVC | Cluster default StorageClass / volume provisioner |
