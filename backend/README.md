# Jimmy backend

FastAPI app with separate configuration, API router, and route modules. Requires
Python 3.10+; the existing project `venv` can be used.

From the repository root:

```sh
venv/bin/python -m pip install -r backend/requirements-dev.txt
venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

- Liveness: `http://127.0.0.1:8000/api/health`
- Swagger documentation: `http://127.0.0.1:8000/api/docs`
- Tests: `venv/bin/python -m pytest -c backend/pytest.ini backend/tests`

`CORS_ORIGINS` is a comma-separated environment variable. Defaults allow only
local frontend origins on port 5173. `.env.example` is a template; to load a copied
`backend/.env`, pass `--env-file backend/.env` to Uvicorn (requires python-dotenv).
No AWS calls, secrets, ingestion, or backtesting run on startup. Health checks
process liveness, not database readiness. Deployment is managed by GitHub Actions.

## AWS deployment

The dev workflow packages `requirements-lambda.txt` and `app/` on a Linux x86_64
runner for Python 3.14. `app.lambda_handler.handler` adapts FastAPI to API Gateway
HTTP API events using Mangum. Uvicorn remains the local server.

Terraform in `infra/dev/backend` provisions Lambda, its log group, a log-only
execution role, and the HTTP API. CloudFront routes `/api/*` to this origin.
The API is currently public and exposes only health and framework documentation;
add authentication before exposing private data or user operations. Lambda is
for HTTP requests, not long-running batch ingestion or backtests.
