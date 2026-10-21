# Jimmy

AI-native quantitative research platform.

## Codebase layout

- `frontend/`: React application.
- `api/`: FastAPI routes and the Lambda entry point for HTTP requests.
- `workers/`: independently runnable background jobs, including the local strategy research runner.
- `packages/`: reusable Python code. `agent_harness/` provides bounded model and tool execution; `agents/strategy_agent/` contains the trading plan, dated decisions, market-data tools, and deterministic replay.
- `batch_scripts/`: locally runnable data ingestion jobs.
- `infra/dev/` and `infra/prod/`: infrastructure for each environment.

The API and each worker will build separately and use only the shared packages they need.
The existing AWS API resources retain their `jimmy-dev-backend` names and Terraform
state key while the code moves to `api/`, so this layout change does not replace them.

The strategy research runner is a finite local job. See [its instructions](workers/strategy_research/README.md)
for the Bedrock-backed strategy test; it is not started by `npm run dev`.

## Run all local services

From the `jimmy` repository root:

```sh
npm run dev
```

This starts the React frontend at http://127.0.0.1:5173 and FastAPI at
http://127.0.0.1:8000, with API docs at http://127.0.0.1:8000/api/docs.
The API uses `venv/bin/python` directly, so activating the venv is optional.
Each service has labelled logs and hot reload. Press Ctrl+C to stop all services.
For the strategy prompt and results view, run `AWS_PROFILE=siby_baskt npm run dev`
so the local API can access DynamoDB and Amazon Bedrock.
If any service exits, the launcher stops the others. Stop separately launched
servers first if they already occupy ports 5173 or 8000.

On a fresh checkout, install dependencies once (Node 24 and Python 3.14):

```sh
python3 -m venv venv
venv/bin/python -m pip install -r api/requirements-dev.txt
venv/bin/python -m pip install -r packages/agents/strategy_agent/requirements.txt
npm ci
npm --prefix frontend ci
```

For alternate local ports:

```sh
API_PORT=18000 FRONTEND_PORT=15173 npm run dev
```

Vite forwards `/api/*` to the chosen API port. These port variables affect
local development only. This command does not run ingestion or deploy AWS resources.

### Add a future local service

Add a long-running `dev:<service>` script in the root `package.json`, for example:

```json
"dev:research": "./venv/bin/python -m workers.research"
```

This is an example for when a long-running worker module exists; no worker is currently
configured. `npm run dev` discovers every `dev:*` script automatically. Install
each service's dependencies before starting it, and give HTTP services distinct
ports. Services start concurrently; future services that depend on each other
must handle readiness/reconnection themselves. Keep finite batch jobs outside
`dev:*`, since a finished service stops the whole group.


## TODO
DynamoDB contains current universe membership, which introduces survivorship bias into historical results.
If stocks dont have a market cap, we take the unused weight, and divide it by the number of stocks without market caps