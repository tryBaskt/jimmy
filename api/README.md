# Jimmy API

FastAPI app with separate configuration, API router, and route modules. Requires
Python 3.10+; the existing project `venv` can be used.

From the repository root, `npm run dev` starts both frontend and API using
the existing venv (install root Node dependencies with `npm ci` once). Press Ctrl+C
to stop both. It also picks up future root scripts named `dev:<service>`.
Use `AWS_PROFILE=siby_baskt npm run dev` to enable local strategy testing.

To run the API independently from the repository root:

```sh
venv/bin/python -m pip install -r api/requirements-dev.txt
JIMMY_LOCAL_STRATEGY=1 venv/bin/python -m uvicorn app.main:app --app-dir api --reload --reload-dir api --reload-dir packages --host 127.0.0.1 --port 8000
```

- Liveness: `http://127.0.0.1:8000/api/health`
- Swagger documentation: `http://127.0.0.1:8000/api/docs`
- Tests: `venv/bin/python -m pytest -c api/pytest.ini api/tests`

`CORS_ORIGINS` is a comma-separated environment variable. Defaults allow only
local frontend origins on port 5173. `.env.example` is a template; to load a copied
`api/.env`, pass `--env-file api/.env` to Uvicorn (requires python-dotenv).
No AWS calls or backtesting run on startup. `GET /api/strategy/capabilities`
reports whether local strategy testing is enabled. `POST /api/strategy/run` accepts a
prompt and synchronously runs the bounded strategy workflow when
`JIMMY_LOCAL_STRATEGY=1`; the root local launcher sets this automatically.
Market-cap weighting uses Yahoo's historical price and share-count estimates
at each prior rebalance close. Yahoo does not verify when those share counts
were first published, so these weights remain exploratory.
Health checks process liveness, not database readiness. Deployment is managed
by GitHub Actions.

## AWS deployment

The dev workflow packages `requirements-lambda.txt` and `app/` on a Linux x86_64
runner for Python 3.14. `app.lambda_handler.handler` adapts FastAPI to API Gateway
HTTP API events using Mangum. Uvicorn remains the local server.

Terraform in `infra/dev/api` provisions Lambda, its log group, a log-only
execution role, and the HTTP API. CloudFront routes `/api/*` to this origin.
The public Lambda reports strategy testing as unavailable and rejects runs. It has no
strategy dependencies or DynamoDB/Bedrock permissions; local runs use the
existing venv and AWS profile. A hosted strategy flow needs authentication and
an asynchronous worker before it can be enabled. Existing AWS
resource names and the Terraform state key still use `backend`; this preserves
the deployed resources during the source layout change.
