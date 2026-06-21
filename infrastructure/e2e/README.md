# End-to-End Integration Validation

Reproducible proof that the full platform works together:

```
manual trigger / GitHub webhook
  → review job queued (Celery + Redis)
  → worker processes review
  → analytics persisted in PostgreSQL
  → GraphQL returns review data
  → dashboard displays analytics (Playwright)
```

External GitHub and OpenAI are replaced by deterministic stubs when
`E2E_INTEGRATION=true` on the worker. All other components (API, worker, Celery,
PostgreSQL, Redis, ChromaDB, GraphQL, frontend) run real application code.

## Prerequisites

- Docker and Docker Compose
- Python 3.11+
- Node.js 18+ (for Playwright dashboard validation)

## 1. Start the E2E stack

From the repository root:

```bash
docker compose -f docker-compose.e2e.yml up --build -d
```

Wait until services are healthy:

```bash
docker compose -f docker-compose.e2e.yml ps
```

Expected ports (host):

| Service  | URL |
|----------|-----|
| API      | http://localhost:8010 |
| Frontend | http://localhost:3010 |
| Postgres | localhost:5433 |
| Redis    | localhost:6380 |

## 2. Run the API integration smoke test

```bash
python scripts/e2e_validate.py
```

This script:

1. Waits for `/health` and `/reviews/health`
2. Queues a review via `POST /reviews/jobs`
3. Polls `GET /reviews/jobs/{job_id}` until the worker completes
4. Sends a signed `POST /webhook` payload
5. Queries GraphQL `reviews` and `reviewStats` for persisted analytics

Environment overrides:

```bash
E2E_API_URL=http://localhost:8010 \
E2E_REPOSITORY=octocat/hello \
E2E_PULL_NUMBER=42 \
python scripts/e2e_validate.py
```

## 3. Run the dashboard integration test (Playwright)

Requires the E2E stack from step 1.

```bash
cd frontend
npm install
npm run test:e2e:install
npm run test:e2e:integration
```

This test uses **no mocks**. It triggers a review through the dashboard UI and
waits for GraphQL-backed analytics to appear in the table.

## 4. Tear down

```bash
docker compose -f docker-compose.e2e.yml down -v
```

## CI

GitHub Actions runs the same flow in `.github/workflows/e2e.yml` on pushes and
pull requests to `main`.

## Stub boundary

| Component | E2E behavior |
|-----------|--------------|
| PostgreSQL, Redis, Celery | Real |
| API, worker, GraphQL | Real |
| GitHub API | Stub (`worker/app/e2e/stub_clients.py`) |
| OpenAI LLM | Stub (`worker/app/e2e/stub_clients.py`) |
| Git clone / checkout | Skipped gracefully (indexing best-effort) |

Enable stubs only with `E2E_INTEGRATION=true` (set in `docker-compose.e2e.yml`).
