# Jimmy

AI-native quantitative strategy testing designed for AWS dev deployment.

- `frontend/`: React site on S3 and CloudFront.
- `api/`: FastAPI on Lambda behind API Gateway. It authenticates users and
  submits strategy jobs to SQS; it does not execute them.
- `workers/strategy_worker/`: ECS Fargate consumer that executes strategy jobs
  and stores reports in S3.
- `packages/`: reusable agent, market-data, and simulation code used by the worker.
- `batch_scripts/`: stock-universe metadata import tooling, separate from the
  interactive strategy-job runtime.
- `infra/dev/` and `infra/prod/`: environment infrastructure.

The dev workflow builds and deploys the application on pushes to `dev` after
the deployment role has the permissions documented in
[`infra/dev/iam/README.md`](infra/dev/iam/README.md). There is no local
application or strategy-job launcher. Tests and build checks can run locally.

Historical backtests currently use today's universe membership, which can
introduce survivorship bias. Market-cap weights use available historical
share-count estimates; if a selected stock lacks a market cap, the remaining
weight is divided among selected stocks without one.
