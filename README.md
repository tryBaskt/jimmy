# Jimmy: paper replication workspace

A ground-up AWS application that identifies a finance paper's main claim and supporting experiment suite, reconstructs supported experiments using available data, records substitutions and caveats, compares published results, and runs exploratory extrapolations before producing a final assessment.

The browser shows plans, connector activity, data decisions, code artifacts, experiment results and comparisons during execution. Interpretation is read-only. Results have exactly two groups: **paper experiments** (original or modified) and **extrapolated experiments**.

## Repository structure

- `apis/projects/`: project creation and ownership-scoped workspace reads.
- `apis/research/`: asynchronous run creation, status, inspection and cancellation.
- `apis/data/`: upload intents, validation and input freezing.
- `apis/artifacts/`: authorized artifact discovery and downloads.
- `apis/activity/`: durable activity replay and single-use WebSocket tickets.
- `apis/identity/`: identity and health endpoints.
- `apis/app.py`: common application factory; production loads one service per Lambda, local development mounts all services.
- `packages/paperlab/`: validated contracts, storage, Bedrock agents, connector registry, catalog, numerical templates and workflow stages.
- `workers/replication/`: non-HTTP ECS stage execution entry point.
- `frontend/`: React/TypeScript workspace.
- `infra/bootstrap/`: one-time GitHub federation, dev/prod deployment roles, ECR repositories and Terraform-state buckets.
- `infra/environments/`: new environment-scoped application infrastructure.
- `tests/`: numerical, catalog, API, workflow and mocked-AWS checks.

No previous application code or deployed resource is an implementation dependency.

## API Gateway and service boundaries

One HTTP API Gateway routes service prefixes to independently configured Lambda functions:

| Prefix | Service |
| --- | --- |
| `/api/projects` | Projects |
| `/api/research/*` | Research |
| `/api/data/*` | Data |
| `/api/artifacts/*` | Artifacts |
| `/api/activity/*` | Activity |
| `/api/identity/*`, `/api/health` | Identity/health |

Production functions share an immutable API image but load only their own router through `API_SERVICE`. Each has its own role, logs and configuration. Shared contracts are an internal Python package. New services add a router and gateway integration without changing the API hostname. Metadata uses a shared table initially, with application-enforced project ownership; this is not independent-database-per-service isolation.

## Supported behavior and limits

- Bedrock Converse uses **`us.openai.gpt-6-astra`** for scientific planning, data-suitability decisions and extrapolations; **`us.moonshotai.kimi-k3`** writes the plain-language evidence assessment. Models are independently configurable. No silent fallback model is used. Account model access must be enabled in the deployment region.
- PDF extraction preserves page references and rejects ungrounded model citations. Scanned/sparse PDFs use a bounded AWS Textract fallback in the AWS deployment. OCR still requires human-scored extraction evaluation; unreadable content fails explicitly.
- Trusted numerical templates implement monthly equal-weight momentum sorts with overlapping cohorts and one explicitly identified monthly return series. Holdings use information before the evaluated month. One-week entry delays, arbitrary regressions, fundamental sorts and other methods are marked unsupported, not approximated invisibly.
- Model-generated Python is never executed. The implementation artifact contains the checked numerical template; unsupported methods remain visible coverage gaps.
- Each stage is executed in an ephemeral Fargate task. Numerical stages have a separate role without Bedrock/secret permissions and a security group without public internet access. They run only trusted templates, not arbitrary uploaded scripts.
- Statistics use HAC standard errors with holding-period-based lags. Returns are before transaction, financing, shorting and market-impact costs. A compounded long-short spread is hypothetical per-dollar-long wealth, not a funded-account return.
- Extrapolations proposed after observed results are **exploratory**. The application does not label them untouched holdout validation. A reserved-holdout training/evaluation protocol is not implemented.
- YAML/PDF/model content cannot create arbitrary tools, execute code, change ownership or directly select arbitrary URLs.

## Data connectors and cache

The registry in `packages/paperlab/connectors/` exposes `discover(requirement, scope)` and `fetch(candidate)`. New connectors implement those methods and register with the workflow; API Gateway changes are unnecessary for another provider.

- **yfinance** retrieves adjusted daily prices and derives monthly returns. Explicit ticker selection is required; current symbols do not establish historical membership. Delisted securities and historical data vintages are not guaranteed. These limitations force qualified, modified paper-experiment results.
- **Bigdata.com** searches financial documents through `POST /v1/search`. Results retain source/entitlement context and cannot masquerade as a complete numerical dataset. Its API key is injected into worker tasks by ECS from Secrets Manager; it is never sent to model prompts or the browser. The initial document connector does not synthesize fundamental time series from narrative text.
- **CSV uploads** support `date,security_id,return`, with decimal monthly returns, unique security-month rows, finite values and no returns below -1. Uploads remain project-private. Upload data before starting a run.

Adapters consult a scoped catalog before retrieval. Exact cached requests reuse immutable object references; concurrent requests coordinate with a fenced acquisition lease. Source refreshes preserve retrieval history, and identical bytes reuse the same canonical object. Different scopes never share object identity. Data revisions get new versions, and each execution freezes its selected manifest. No project-deletion/garbage-collection endpoint is exposed yet; retained source snapshots are not automatically expired.

Cross-user Yahoo caching is controlled by `YFINANCE_SHARED_CACHE` (default false). Enable it only when the provider's terms permit platform-wide reuse. Bigdata content stays project-scoped initially. Compatible superset snapshots can be reused with explicit security/date filters; partial-range acquisition and automated garbage collection remain future optimizations. Retained object references are recorded now.

## Local development

Requires Python 3.12+ and Node 22+.

```sh
python3 -m venv .venv
.venv/bin/pip install -c requirements.lock -e '.[dev]'
.venv/bin/uvicorn apis.app:app --host 127.0.0.1 --port 8000
```

In another terminal:

```sh
npm --prefix frontend ci
npm --prefix frontend run dev
```

Open `http://localhost:5173`. Local mode uses a loopback-only development identity, SQLite metadata and local immutable object files under `.paperlab/`. It requires AWS credentials with Bedrock access for actual AI runs. Numerical tests do not require credentials or internet. `PLANNER_MODEL_ID`, `WORKER_MODEL_ID` and `AWS_REGION` configure Bedrock. For local Bigdata testing, inject `BIGDATA_API_KEY` through your secret manager; do not commit an `.env` file or print the key.

The local runner is for trusted development only; AWS uses Fargate. Closing the browser does not cancel an AWS run. Cancellation persists immediately and is checked between stages/experiments; terminating the state-machine execution requests cancellation of its running service integration.

## Bootstrap AWS once

This is a new deployment, not an import of previous infrastructure. NAT gateways and private endpoints incur charges while idle.

```sh
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap plan
terraform -chdir=infra/bootstrap apply
```

If your account already has GitHub's OIDC provider, supply its ARN through `github_oidc_provider_arn`. Keep bootstrap state securely; move it to a separately managed remote backend after initialization. The bootstrap creates `jimmy-dev-*` and `jimmy-prod-*` deployment foundations. Runtime resources follow `jimmy-<env>-<purpose>`; globally unique buckets/domains additionally include the account ID. AWS-managed rules and service-linked roles retain their service-defined names.

Use the appropriate operator `AWS_PROFILE` when running bootstrap. Account-specific settings can be kept in ignored `infra/bootstrap/local.auto.tfvars`. The `repository` input must match the repository portion of GitHub's actual OIDC subject, including `owner@owner_id/repo@repo_id` when ID-based subjects are emitted. Preserve this local configuration along with the ignored bootstrap state for subsequent applies. The new application's frontend uses `jimmy-<env>-workspace-frontend-<account>` to avoid the legacy frontend bucket name.

The bootstrap role policies are deployment-operator policies, broader than runtime policies. They can provision networking, CloudFront and gateways and should be administered only by trusted operators; use separate accounts/permission boundaries for strict dev/prod control. They cannot modify the other environment's application IAM role path. Runtime roles have narrower table, bucket, model and execution permissions.

## Separate dev and production deployment

Create GitHub environments `dev` and `prod`. Set these **environment secrets** in each:

| Secret | Value |
| --- | --- |
| `AWS_ACCOUNT_ID` | Deployment account |
| `AWS_DEPLOY_ROLE_ARN` | Matching bootstrap deployment-role output |
| `BIGDATA_SECRET_ARN` | Optional **actual Bigdata API key**; this existing GitHub name is retained for compatibility |
| `ALERT_EMAIL` | Optional email to subscribe to failure alerts; confirm the SNS subscription |

Set `AWS_REGION` as an **environment variable** (default `us-east-1`; must support both Bedrock profiles). `YFINANCE_SHARED_CACHE` is also an optional environment variable: `true` only for authorized shared reuse; otherwise `false`.

Deployment securely creates or updates `jimmy-<env>-bigdata-api-key` and `jimmy-<env>-alert-email` in AWS Secrets Manager from the GitHub secrets. The misleading GitHub name `BIGDATA_SECRET_ARN` contains the key; the workflow maps it to a step-local `BIGDATA_API_KEY`, then passes only the resulting **AWS ARN** to Terraform. ECS injects the API key into worker tasks at runtime. You do not need to create the AWS secrets manually. Bootstrap permissions allow writing only these two named secrets for the matching environment, without reading their values. **If you already applied bootstrap, apply it again before deploying this change** to grant those permissions.

The alert recipient is configured through SNS after infrastructure deployment, outside Terraform, so the address is absent from Terraform plans/state. SNS must know the destination to deliver alerts; confirm its email subscription. The dedicated alerts topic has one deployment-managed email recipient; changing the GitHub email replaces confirmed old email subscriptions. Pending old requests cannot deliver until confirmed; remove obsolete pending requests in SNS when changing addresses. Omitting an optional GitHub secret preserves the existing AWS secret; omitting the email preserves existing subscriptions. Removing the Bigdata GitHub secret disables connector injection on the next deployment but does not delete the retained AWS secret.

Private values are sent directly from step-scoped environment variables to the AWS SDK, not command arguments, files, Terraform inputs/state or job output. Never place them in GitHub variables, prompts or this repository. The sync uses the AWS-managed Secrets Manager encryption key; customer-managed keys require additional write/decrypt permissions. Changing a GitHub secret takes effect on the next environment deployment, which creates a new stored version; newly launched worker tasks receive it, while already-running tasks retain their injected value. Repeated syncs create versions; do not run this as a high-frequency rotation loop. Terraform destroy does not remove these deployment-managed secrets; retire them separately when decommissioning an environment. Deleting the SNS topic removes its subscriptions.

- **Merge to `dev`:** `deploy-dev.yml` runs CI, builds/pushes both images, applies the dev infrastructure, builds/uploads the frontend, waits for cache invalidation and smoke-checks the API/configuration. Its Actions summary contains the **frontend URL**. Terraform also outputs it.
- **Production:** manually dispatch `deploy-prod.yml` from `main`. Configure required reviewers and branch restrictions on the `prod` GitHub environment. Production uses separate state, image repositories, identity, data and compute.
- The workflows never merge branches. Branch protection should require the CI checks before merging.

Cognito sign-up is invitation-only. After the first deployment, an administrator creates a user with the new user-pool ID output:

```sh
aws cognito-idp admin-create-user --user-pool-id YOUR_NEW_POOL_ID --username YOUR_EMAIL --user-attributes Name=email,Value=YOUR_EMAIL Name=email_verified,Value=true
```

The invitation lets you establish your password and access the deployed frontend through Cognito's hosted sign-in. Until bootstrap and GitHub environment settings are configured, merging alone cannot create AWS credentials for GitHub Actions.

## Checks

```sh
.venv/bin/ruff check apis packages workers scripts tests
.venv/bin/pytest -q
npm --prefix frontend run build
terraform fmt -check -recursive infra
terraform -chdir=infra/environments init -backend=false
terraform -chdir=infra/environments validate
terraform -chdir=infra/bootstrap init -backend=false
terraform -chdir=infra/bootstrap validate
```

Tests cover numerical timing/overlapping cohorts, missing holdings, non-comparable modified results, atomic event cursors, shared cache hits, leases, immutable revisions, private uploads, API service boundaries, authorization, workflow cancellation and AWS-store parity using Moto. Cloud credentials, actual model behavior, source entitlements and a real AWS deployment require separate integration verification.

See [architecture](docs/paper-replication-architecture.md) for the full target design and [implementation status](docs/implementation-status.md) for what is executable versus planned.
