# Helm deployment

Helm chart for the AI Code Review platform. It templates the same Kubernetes resources as [`../k8s/`](../k8s/) with configurable values for images, replicas, resources, ports, and environment variables.

## Prerequisites

- Kubernetes cluster (local or cloud)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [Helm 3](https://helm.sh/docs/intro/install/)
- Container images built locally or pushed to a registry:
  - `code-review-api:latest`
  - `code-review-worker:latest`
  - `code-review-frontend:latest`

Build images from the repository root:

```bash
docker build -t code-review-api:latest ./api
docker build -t code-review-worker:latest ./worker
docker build -t code-review-frontend:latest ./frontend
```

For minikube or kind, load images into the cluster after building.

## Chart location

```
infrastructure/helm/ai-code-review/
├── Chart.yaml
├── values.yaml
└── templates/
```

## Quick install

From the repository root:

```bash
helm lint infrastructure/helm/ai-code-review

helm install code-review infrastructure/helm/ai-code-review \
  --namespace code-review \
  --create-namespace
```

Set secrets at install time (recommended):

```bash
helm install code-review infrastructure/helm/ai-code-review \
  --namespace code-review \
  --create-namespace \
  --set secret.postgresPassword='your-secure-password' \
  --set secret.openaiApiKey='sk-...' \
  --set secret.githubToken='ghp_...' \
  --set secret.githubWebhookSecret='your-webhook-secret'
```

Or use a custom values file:

```bash
cp infrastructure/helm/ai-code-review/values.yaml my-values.yaml
# edit my-values.yaml — set secret.* and env.* fields

helm install code-review infrastructure/helm/ai-code-review \
  --namespace code-review \
  --create-namespace \
  -f my-values.yaml
```

## Upgrade and uninstall

```bash
helm upgrade code-review infrastructure/helm/ai-code-review -f my-values.yaml

helm uninstall code-review --namespace code-review
```

## Render manifests (dry run)

Compare rendered output with plain manifests:

```bash
helm template code-review infrastructure/helm/ai-code-review \
  --namespace code-review > /tmp/helm-rendered.yaml
```

Validate structure:

```bash
helm lint infrastructure/helm/ai-code-review
```

## Configuration reference

All defaults mirror `infrastructure/k8s/`. Override via `--set` or a values file.

| Area | Values path | Description |
|------|-------------|-------------|
| Namespace | `namespace.name`, `namespace.create` | Target namespace |
| ConfigMap | `configMap.name`, `env.*` | Non-secret environment defaults |
| Secret | `secret.*` | Credentials and API keys |
| Images | `<component>.image.repository`, `<component>.image.tag` | Per-service image |
| Replicas | `<component>.replicaCount` | Deployment replica count |
| Resources | `<component>.resources` | CPU/memory requests and limits |
| Ports | `<component>.service.port`, `<component>.service.targetPort` | Service and container ports |
| Toggle components | `<component>.enabled` | Enable/disable postgres, redis, chromadb, api, worker, frontend |

### Auto-built URLs

When left empty, these are derived from in-cluster service names:

- `env.redisUrl` → `redis://redis:6379/0`
- `env.chromaUrl` → `http://chromadb:8000`
- `secret.databaseUrl` → `postgresql://<user>:<password>@postgres:5432/<db>`

### Frontend API URL

`NEXT_PUBLIC_API_URL` is baked into the frontend image at build time. The ConfigMap value documents the intended browser-facing URL; rebuild the frontend image with the correct build arg when deploying behind ingress or port-forward.

### External secrets

To manage secrets outside Helm, disable chart secret creation and create `code-review-secrets` manually (see [`../k8s/secret.example.yaml`](../k8s/secret.example.yaml)):

```bash
helm install code-review infrastructure/helm/ai-code-review \
  --set secret.create=false
```

## Verify deployment

```bash
kubectl -n code-review get pods
kubectl -n code-review get svc

kubectl -n code-review port-forward svc/api 8000:8000
kubectl -n code-review port-forward svc/frontend 3000:3000
```

- API health: http://localhost:8000/health
- Dashboard: http://localhost:3000/dashboard

## Plain manifests alternative

For environments without Helm, apply manifests directly:

```bash
kubectl apply -f infrastructure/k8s/
```

See [`../k8s/README.md`](../k8s/README.md) for kubectl-based instructions.
