# Dev infrastructure

This Terraform root manages dev resources in AWS account `499133675835`, region
`us-east-1`. Resource names follow `jimmy-<env>-<purpose>`.

## Stock metadata table

- Name: `jimmy-dev-stock-metadata`
- Partition key: `symbol` (String), containing the FMP symbol
- Capacity: on-demand (`PAY_PER_REQUEST`)
- No sort key, secondary indexes, TTL, or initial items
- Encryption at rest uses DynamoDB's default AWS-owned key

Only key attributes are declared in Terraform. The ingestion job will write
`companyName`, `exchange`, `country`, `state`, `city`, `sector`, `industry`,
`currency`, `marketCap`, `isActivelyTrading`, `universes`, and `updatedAt` as
ordinary item attributes. These represent current metadata, not historical
index membership. Missing FMP values can be omitted.

## Stock universe membership table

- Name: `jimmy-dev-stock-universe-membership`
- Partition key: `universe` (String), such as `SP500` or `NASDAQ100`
- Sort key: `symbol` (String), containing the FMP symbol
- Capacity: on-demand (`PAY_PER_REQUEST`)
- No secondary indexes, TTL, or initial items

Each item contains only a universe and symbol pair. The composite key makes each
pair unique while allowing a symbol to belong to multiple universes. Query by
`universe` to retrieve its stock symbols. Reverse lookup by symbol is not indexed.
These are current memberships; they do not include historical effective dates.
Keep ETF-derived Russell memberships identified as proxies in ingestion and UI.

Both table names and ARNs are exposed as Terraform outputs. Ingestion and prod
infrastructure are outside this deployment.

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
5. Verifies both tables exist and records their names and ARNs in the job logs.

State bucket: `jimmy-dev-terraform-state-499133675835`.
State key: `dev/terraform.tfstate`; locking uses `dev/terraform.tfstate.tflock`.
The versioned state persists across workflow runs. No DynamoDB locking table is
needed. Workflow concurrency and S3 locking prevent simultaneous applies.

The workflow bootstraps the state bucket outside this Terraform root, since the
backend must exist before Terraform initializes. The bucket is retained across
runs and is not deleted by a table destroy. Do not delete or rename it without
planning a state migration. Backend operations use the default Terraform workspace.

[Dev IAM](iam/README.md) documents the two resource-scoped policies on the single
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
The local ingestion script reads the value at runtime using the local AWS profile.

## Application provisioning

The same dev workflow now runs these jobs in order:

`CI` → **provisioning infra** → **provisioning backend** → **provisioning frontend**

The original root still manages DynamoDB and the FMP secret metadata. The child
roots [`backend/`](backend/README.md) and [`frontend/`](frontend/README.md) own
separate application resources and state files; Terraform does not recursively
apply child directories. No existing resources or state addresses are moved.
Every job uses `jimmy-dev-github-actions` and the protected `dev` environment.
The final job writes the deployed frontend URL to the workflow summary.

Before running the expanded workflow, an administrator must update the existing
role's `jimmy-dev-terraform-state` inline policy from `iam/terraform-state.json`
and add `iam/application-provisioning.json` as `jimmy-dev-application-provisioning`.
The workflow cannot grant itself these additional permissions. See the IAM README.
Prod is not deployed by this workflow; `infra/prod` remains independent.
