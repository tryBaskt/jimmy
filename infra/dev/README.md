# Dev infrastructure

This Terraform root manages dev resources in AWS account `499133675835`, region
`us-east-1`. Resource names follow `jimmy-<env>-<purpose>`.

## Historical stock tables

The three on-demand tables have String keys and no secondary indexes or TTL:

| Table | Partition key | Sort key |
|---|---|---|
| `jimmy-dev-stock-metadata` | `security_id` | none |
| `jimmy-dev-stock-universe-membership` | `universe` | `membership_key` (`security_id#observed_from`) |
| `jimmy-dev-stock-gics-history` | `security_id` | `classification_key` |

Metadata uses a stable security ID, a source display `symbol`, company details,
`source_symbol_observations`, and verified `ticker_aliases`. A verified alias has
`symbol`, `effective_from`, optional exclusive `effective_to`, and evidence.
Source symbols with only observed bounds are not verified historical exchange
tickers. Share classes remain separate securities; unresolved matches are not merged.

Membership rows contain `security_id`, `source_symbol`, `added_at`, optional
exclusive `removed_at`, observed interval bounds, and dataset coverage bounds
`dataset_observed_from`/`dataset_observed_through`. CSV dates are snapshot
observations, not official membership effective dates. Left/right censoring flags
distinguish dataset boundaries from real changes.

GICS rows store only classifications permitted under the Bigdata agreement.
`classification_key` distinguishes verified effective dates from observation-only
records. Observed classifications must not be backdated to membership dates.
Missing historical classifications remain unknown.

The universe reader defaults to the last dataset snapshot and resolves display
symbols through metadata with BatchGetItem. Explicit historical requests reject
dates outside dataset coverage and require verified effective ticker aliases.
The strategy workflow still selects one universe for an entire run; this migration
does not add per-rebalance historical membership selection. Backtests must not be
presented as free of survivorship bias until that workflow is implemented.

Changing the first two tables' keys requires replacement. Export their existing
contents before replacement. Names and ARNs are exposed as Terraform outputs.
The old `universe_stock_metadata_ingestion` utility targets the retired symbol-only
schema and fails its schema check against these tables; use historical ingestion.
Only key attributes are declared in Terraform. Encryption at rest uses DynamoDB's
default AWS-owned key.

## Deployment

The `Dev infrastructure` workflow runs on pushes to `dev` and manual dispatches
from `dev`. After CI passes, its job **provisioning infra**:

1. Checks out the triggering commit and authenticates using
   `jimmy-dev-github-actions` through OIDC.
2. Creates the dedicated state bucket if absent. An existing inaccessible bucket
   causes failure rather than being treated as absent.
3. Enforces private access, versioning, SSE-S3 encryption, tags, and HTTPS-only
   access on the bucket.
4. Initializes the S3 backend, validates Terraform, saves a plan, and applies that
   exact plan.
5. Verifies all three stock tables exist and records their names and ARNs in the job logs.

State bucket: `jimmy-dev-terraform-state-499133675835`.
State key: `dev/terraform.tfstate`; locking uses `dev/terraform.tfstate.tflock`.
The versioned state persists across workflow runs. No DynamoDB locking table is
needed. Workflow concurrency and S3 locking prevent simultaneous applies.

The workflow bootstraps the state bucket outside this Terraform root, since the
backend must exist before Terraform initializes. The bucket is retained across
runs and is not deleted by a table destroy. Do not delete or rename it without
planning a state migration. Backend operations use the default Terraform workspace.

[Dev IAM](iam/README.md) documents the provisioning policies on the single
existing deployment role. The GitHub `dev` environment must allow the `dev` branch.
PR validation never receives AWS credentials or applies resources.

## Local validation

Run `terraform fmt -check` and `terraform init -backend=false -input=false
-lockfile=readonly`, followed by `terraform validate`, from this directory.
Deployment must run through GitHub Actions. Do not apply locally or commit state
or plan files.

## FMP API secret

`secrets.tf` imports and manages the metadata of `jimmy-dev-fmp-api-key`. Its
value was uploaded directly from the local `.env` using an administrator session.
No secret-version resource, secret-value data source, or value output is used,
so Terraform does not read or store the API key. Destruction is prevented.
The separate metadata ingestion utility reads the value at runtime using AWS credentials.

## Application provisioning

The same dev workflow now runs these jobs in order:

`CI` → **provisioning infra** → **provisioning api** → **provisioning frontend**

The original root still manages DynamoDB and the FMP secret metadata. The child
roots [`api/`](api/README.md) and [`frontend/`](frontend/README.md) own
separate application resources and state files; Terraform does not recursively
apply child directories. The API root still uses `dev/backend/terraform.tfstate`
and the same resource addresses and AWS names, so moving its folder does not
replace existing resources.
Every job uses `jimmy-dev-github-actions` and the protected `dev` environment.
The final job writes the deployed frontend URL to the workflow summary.

## Authenticated async strategy jobs

This root also owns `jimmy-dev-user-sessions` and `jimmy-dev-strategy-jobs`
(DynamoDB), `jimmy-dev-strategy-jobs` and its dead-letter queue (SQS), a private
30-day results bucket, and an ECR repository. The frontend root owns the dev
Cognito user pool, managed-login domain and web client, whose callback is the
CloudFront URL. The API root adds auth/session and job permissions to the
existing Lambda. The independent [`worker/`](worker/) root owns a two-subnet
VPC, ECS cluster, one Fargate service, task roles and logs. Its tasks have
outbound HTTPS and no inbound route from the internet.

The workflow now also builds and deploys the worker after base infrastructure,
then reapplies the API after the frontend produces the Cognito callback URL and
client ID. The deployment policies have been applied to the existing dev role;
see [dev IAM](iam/README.md) for their scope and future updates.

The workflow cannot grant its own role additional permissions. Those policies
were applied and verified on 2026-10-22. Future policy changes still require
administrator review. See the IAM README.
Prod is not deployed by this workflow; `infra/prod` remains independent.
