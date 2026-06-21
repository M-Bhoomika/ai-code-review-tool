# Playwright End-to-End Tests

Browser-based E2E tests for the dashboard and API integration live in
`frontend/e2e/`.

## Prerequisites

- Node.js 18+
- Python 3.11 with the API dependencies installed (`pip install -r ../api/requirements.txt`)
- Playwright Chromium browser

## Setup

From the `frontend/` directory:

```bash
npm install
npm run test:e2e:install
```

## Running the tests

The Playwright config starts two local services automatically:

1. **API** on `http://localhost:8000` (SQLite database for isolated E2E runs)
2. **Frontend** on `http://localhost:3000`

Run the full suite:

```bash
npm run test:e2e
```

Other useful commands:

```bash
# Interactive UI mode
npm run test:e2e:ui

# Headed browser (watch tests run)
npm run test:e2e:headed
```

If you already have the API and frontend running locally, Playwright reuses
them instead of starting new processes (unless `CI=true`).

Override service URLs when needed:

```bash
E2E_API_URL=http://localhost:8000 E2E_FRONTEND_URL=http://localhost:3000 npm run test:e2e
```

## What is covered

| Spec | Coverage |
| ---- | -------- |
| `e2e/api-health.spec.ts` | `/health` and `/reviews/health` respond successfully |
| `e2e/dashboard.spec.ts` | Dashboard page loads with stats, form, and job sections |
| `e2e/review-listing.spec.ts` | Review jobs table renders populated and empty states |
| `e2e/manual-review.spec.ts` | Trigger form validation and manual review queue workflow |

Selectors use stable roles and labels (`getByRole`, `getByLabel`) rather than
CSS classes or arbitrary timeouts.

## Troubleshooting

- **Port already in use**: stop existing services on ports `3000` and `8000`, or
  set `CI=true` to force Playwright to start fresh servers.
- **Missing Python packages**: install API requirements before running E2E.
- **Missing browser**: run `npm run test:e2e:install`.

Test artifacts (`playwright-report/`, `test-results/`) are gitignored.

## Full-stack integration test (no mocks)

For end-to-end validation against the Docker Compose stack (PostgreSQL, Celery,
worker, GraphQL), see [`../infrastructure/e2e/README.md`](../infrastructure/e2e/README.md).

After starting the E2E stack:

```bash
npm run test:e2e:integration
```

This runs `e2e/integration.spec.ts` against `http://localhost:3010` by default.
Override with `E2E_INTEGRATION_FRONTEND_URL` if needed.
