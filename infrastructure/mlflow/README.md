# MLflow experiment tracking

The worker logs every review pipeline execution to MLflow when tracking is
enabled. Each run captures review parameters, metrics, and artifacts so you can
compare models, review modes, and finding rates over time.

## What gets logged

| Type | Name | Description |
| ---- | ---- | ----------- |
| Param | `repository` | GitHub repository (e.g. `octocat/hello`) |
| Param | `pr_number` | Pull request number |
| Param | `model_name` | LLM model used for review generation |
| Param | `review_mode` | `inline` or `langgraph` |
| Metric | `processing_time` | Pipeline duration in milliseconds |
| Metric | `total_findings` | Total generated review comments |
| Metric | `security_findings` | Security-category findings |
| Metric | `performance_findings` | Performance-category findings |
| Metric | `logic_findings` | Logic-category findings |
| Metric | `comments_published` | Comments published to GitHub |
| Artifact | `review_summary.txt` | Human-readable run summary |
| Artifact | `findings.json` | Generated review comments as JSON |

LangGraph runs use per-category counts from the graph state. Inline runs classify
comments heuristically from title and explanation text.

## Environment variables

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `MLFLOW_ENABLED` | `true` | Set `false` to disable tracking |
| `MLFLOW_TRACKING_URI` | `http://mlflow:5000` | MLflow tracking server URL |
| `MLFLOW_EXPERIMENT_NAME` | `ai-code-review` | Experiment name for review runs |

MLflow 3.5+ validates Host headers when listening on `0.0.0.0`. Docker Compose
and Kubernetes manifests configure `--allowed-hosts` so in-cluster workers and
local validation scripts can reach the tracking server.

## Docker Compose

The main `docker-compose.yml` stack includes an MLflow server on port 5000. The
worker mounts the shared `mlflow_data` volume at `/mlflow` so artifacts written
by the worker are available to the tracking server.

```yaml
MLFLOW_TRACKING_URI: http://mlflow:5000
MLFLOW_EXPERIMENT_NAME: ai-code-review
```

Open the MLflow UI at http://localhost:5000 after triggering a review.

## Kubernetes and Helm

Plain manifests: `infrastructure/k8s/mlflow.yaml` deploys MLflow with a shared
`mlflow-data` PVC. The worker mounts the same claim at `/mlflow`.

Helm chart values under `mlflow.enabled` mirror the same deployment.

## Validation

Start the stack and run the validation script:

```bash
docker compose up --build -d
python scripts/mlflow_validate.py
```

Against the E2E stack (stub GitHub/LLM, API on 8010, MLflow on 5001):

```bash
docker compose -f docker-compose.e2e.yml up --build -d
E2E_API_URL=http://localhost:8010 MLFLOW_TRACKING_URI=http://localhost:5001 \
  python scripts/mlflow_validate.py
```

The script triggers a review job, waits for completion, then verifies the MLflow
run contains the required params, metrics, and artifacts.

## Tests

```bash
cd worker
pytest tests/test_mlflow_tracker.py -v
```
