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
