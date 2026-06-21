# OpenTelemetry + Jaeger Tracing

Distributed tracing is exported over OTLP/gRPC to Jaeger from both the API and
worker services.

## Architecture

```
Browser / GitHub webhook
  → FastAPI (auto-instrumented HTTP spans)
  → Celery dispatch (trace context propagated)
  → Worker task (auto-instrumented + manual pipeline spans)
  → OTLP exporter → Jaeger (:4317)
```

Manual spans in the worker:

| Span | Location |
|------|----------|
| `review.job` | `worker/app/tasks.py` |
| `review.pipeline` | `worker/app/review_pipeline.py` |
| `review.pipeline.diff_processing` | diff fetch + parse stage |
| `review.pipeline.indexing` | ChromaDB indexing stage |
| `review.pipeline.analysis` | context retrieval + LLM review |
| `review.pipeline.publishing` | GitHub comment publish stage |

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_ENABLED` | `true` | Set `false` to disable tracing |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://jaeger:4317` | Jaeger OTLP gRPC endpoint |
| `OTEL_EXPORTER_OTLP_INSECURE` | `true` | Use plaintext gRPC (local/dev) |
| `OTEL_SERVICE_NAME` | service default | Override service name in Jaeger |

Docker Compose sets service names to `ai-code-review-api` and
`ai-code-review-worker`.

## View traces in Jaeger

1. Start the stack:

```bash
docker compose up --build -d
```

2. Trigger activity (manual review from the dashboard or API):

```bash
curl -X POST http://localhost:8000/reviews/jobs \
  -H "Content-Type: application/json" \
  -d '{"repository":"octocat/hello","pull_number":42,"installation_id":0}'
```

3. Open Jaeger UI: [http://localhost:16686](http://localhost:16686)

4. Select a service (`ai-code-review-api` or `ai-code-review-worker`) and click
   **Find Traces**.

5. Open a worker trace to see the nested pipeline spans (`review.pipeline.*`).

For the E2E compose stack, Jaeger UI is on port **16687**:
[http://localhost:16687](http://localhost:16687)

## Validation

### Unit tests

```bash
cd api && pip install -r requirements.txt && pytest tests/test_tracing.py -v
cd ../worker && pip install -r requirements.txt && pytest tests/test_tracing.py -v
```

### Live Jaeger verification

With the main stack running:

```bash
python scripts/tracing_validate.py
```

Against the E2E stack:

```bash
docker compose -f docker-compose.e2e.yml up --build -d
E2E_API_URL=http://localhost:8010 JAEGER_QUERY_URL=http://localhost:16687 \
  python scripts/tracing_validate.py
```

The script queues a review, waits for completion, then queries the Jaeger HTTP
API to confirm both services registered and worker traces contain
`review.pipeline` spans.

## Kubernetes / Helm

- Plain manifests: `infrastructure/k8s/jaeger.yaml` + OTEL env in
  `infrastructure/k8s/configmap.yaml`
- Helm chart: `jaeger.enabled` in `infrastructure/helm/ai-code-review/values.yaml`

Apply Jaeger before API/worker so OTLP export succeeds on startup.
