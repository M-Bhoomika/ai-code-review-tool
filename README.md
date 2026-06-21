# AI Code Review Tool

An AI-powered code review platform that automatically reviews GitHub pull
requests, processes work asynchronously, orchestrates analysis with LangGraph
workflows, and surfaces insights through an analytics dashboard.

## What It Does

- Installs as a GitHub App and listens for pull request webhook events.
- Queues incoming events and runs reviews asynchronously with background workers.
- Orchestrates review logic as composable LangGraph workflows.
- Persists results and exposes metrics for an analytics dashboard.

## Architecture Overview

The project is a monorepo composed of independently deployable services:

- **api/** — FastAPI service that handles HTTP requests and GitHub webhooks.
- **worker/** — Celery worker that processes review jobs asynchronously.
- **frontend/** — Next.js (TypeScript) web app with the landing page and dashboard.
- **infrastructure/** — Configuration for observability tooling (Prometheus, Grafana).

Data and messaging flow through PostgreSQL (relational data), Redis (Celery
broker and result backend), and ChromaDB (vector store for embeddings).

### PostgreSQL Persistence Layer

The API uses a production-grade persistence layer built on SQLAlchemy 2.0
(typed, declarative models) with Alembic-managed schema migrations:

- **`app/database/`** — declarative `Base`, the pooled SQLAlchemy engine, the
  `SessionLocal` factory, and a `get_db` request-scoped dependency.
- **`app/models/`** — ORM models for `Repository`, `Review`, and
  `ReviewComment`, including their relationships and indexes.
- **`app/schemas/`** — Pydantic schemas used to validate and serialize API
  payloads for repositories and reviews.
- **`alembic/`** — Alembic environment and versioned migrations; the initial
  migration provisions all three tables with their indexes and foreign keys.

Core entity relationships:

- A `Repository` has many `Review`s (one per pull request commit reviewed).
- A `Review` has many `ReviewComment`s (individual findings on the diff).

Connections use pooling with pre-ping health checks, and the API verifies
database connectivity on startup, logging a warning and continuing in a
degraded mode if the database is temporarily unavailable.

#### Migrations run automatically

When the API container starts it runs `alembic upgrade head` before launching
the server (see `api/entrypoint.sh`), so the schema is provisioned (and kept up
to date) on every `docker compose up` with **no manual steps**. The worker waits
for the API to become healthy, guaranteeing the tables exist before it processes
any jobs.

To run migrations manually for local (non-Docker) development:

```bash
cd api
alembic upgrade head
```

## GitHub Authentication

The worker supports two authentication strategies, selected with the
`USE_GITHUB_APP_AUTH` flag:

- **GitHub App installation tokens (`USE_GITHUB_APP_AUTH=true`)** — the worker
  authenticates as the installed GitHub App. It builds a short-lived RS256 JWT
  (`iss` = `GITHUB_APP_ID`, with `iat`/`exp`) signed by `GITHUB_PRIVATE_KEY`,
  exchanges it for a per-installation access token via the GitHub API, and caches
  that token in-process until shortly before it expires (refreshing
  automatically). Implemented in `worker/app/github/github_app_auth.py`.
- **Personal access token (default, `USE_GITHUB_APP_AUTH=false`)** — the worker
  uses the static `GITHUB_TOKEN`. This is the simplest path for local testing.

Required environment variables:

| Variable               | Used when                | Purpose                                            |
| ---------------------- | ------------------------ | -------------------------------------------------- |
| `USE_GITHUB_APP_AUTH`  | always                   | `true` for App auth, `false` (default) for PAT     |
| `GITHUB_APP_ID`        | App auth                 | GitHub App ID (JWT issuer)                         |
| `GITHUB_PRIVATE_KEY`   | App auth                 | App private key (PEM; `\n`-escaped is accepted)    |
| `GITHUB_TOKEN`         | PAT auth                 | Token to read PRs and publish review comments      |
| `GITHUB_API_URL`       | optional                 | GitHub Enterprise API base URL                     |

Both paths return a standard authenticated PyGithub client, so the review
pipeline (reading PRs, publishing reviews) works identically regardless of which
strategy is active. The installation id from the webhook payload is used to mint
the correct installation token in App mode.

## Local Development Services

`docker-compose.yml` provisions the full local stack:

| Service    | URL / Port                         | Purpose                          |
| ---------- | ---------------------------------- | -------------------------------- |
| api        | http://localhost:8000              | FastAPI backend                  |
| frontend   | http://localhost:3000              | Next.js web app                  |
| postgres   | localhost:5432                     | Relational database              |
| redis      | localhost:6379                     | Celery broker / result backend   |
| chromadb   | http://localhost:8001 (→ 8000)     | Vector store                     |
| worker     | (no exposed port)                  | Background job processing        |
| prometheus | http://localhost:9090              | Metrics collection               |
| grafana    | http://localhost:3001              | Dashboards (admin / admin)       |
| jaeger     | http://localhost:16686             | Distributed tracing (OTLP on :4317) |
| mlflow     | http://localhost:5000              | Experiment tracking              |

All services run locally using free, open-source images.

## Tech Stack

- **Backend:** Python 3.11, FastAPI, Uvicorn, pydantic-settings
- **Persistence:** SQLAlchemy 2.0, Alembic, PostgreSQL (psycopg 3)
- **Worker:** Celery, Redis
- **Frontend:** Next.js 14, React 18, TypeScript (strict)
- **Data:** PostgreSQL, Redis, ChromaDB
- **Observability:** Prometheus, Grafana, Jaeger
- **ML Ops:** MLflow

## Setup

1. Copy `.env.example` to `.env`. The defaults are sufficient to boot the full
   stack locally. To enable real reviews, set `GITHUB_TOKEN` (a PAT the worker
   uses to read PRs and publish comments) and `OPENAI_API_KEY` (for LLM review
   generation). Without them the pipeline runs but degrades to zero comments.
2. Start the stack with `docker compose up` (see below). Database migrations are
   applied automatically — no manual Alembic commands are required.

LangGraph is the default review orchestration engine (`USE_LANGGRAPH=true` in
`.env.example`). Set `USE_LANGGRAPH=false` to use the legacy inline pipeline.

## Running Locally

Start the full stack:

```bash
cp .env.example .env
docker compose up --build
```

That's it. On startup the API container applies all database migrations
(`alembic upgrade head`) and then serves on http://localhost:8000; the dashboard
is available at http://localhost:3000/dashboard. No manual database setup is
needed — the schema is created on first run and the Postgres data is persisted
in the `postgres_data` volume across restarts.

### Tests

Tests use an in-memory SQLite database, so no running PostgreSQL is required.
Run each service's suite from its own directory:

```bash
# API
cd api
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows
pip install -r requirements.txt
pytest

# Worker
cd ../worker
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
pytest
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

See [`frontend/E2E.md`](frontend/E2E.md) for Playwright end-to-end test setup and
commands.

For full-stack integration validation (Docker Compose + GraphQL + worker),
see [`infrastructure/e2e/README.md`](infrastructure/e2e/README.md).

For OpenTelemetry + Jaeger tracing setup and validation,
see [`infrastructure/tracing/README.md`](infrastructure/tracing/README.md).

For Prometheus + Grafana metrics setup and validation,
see [`infrastructure/prometheus/README.md`](infrastructure/prometheus/README.md).

For MLflow experiment tracking setup and validation,
see [`infrastructure/mlflow/README.md`](infrastructure/mlflow/README.md).
