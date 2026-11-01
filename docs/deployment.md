# GitHub deployment setup

Application deployment is triggered only by a push to `dev` or `prod`. Use branch
protection to require a reviewed PR and successful CI, and restrict each GitHub
environment to its matching branch. The workflow has no `workflow_dispatch` trigger.
It does not deploy from pull requests or feature branches.

## Merge to deploy

For dev, the one-time bootstrap is complete: `jimmy-dev-github-deploy` trusts the
GitHub OIDC provider for `tryBaskt/jimmy` and the `dev` environment. Its ARN is derived
from the environment's `AWS_ACCOUNT_ID` secret and branch name; no `AWS_ROLE_ARN`
setting is needed. The bootstrap role and workflow permissions boundary are defined
in `infra/github/` and are intentionally outside the application deployment pipeline.
Only the dev bootstrap has been provisioned; prod needs its own equivalent bootstrap
before its first merge. The GitHub OIDC provider is shared account infrastructure.

The existing GitHub environment secrets are used directly:

| Setting | Kind | Value |
| --- | --- | --- |
| `AWS_ACCOUNT_ID` | Environment secret | AWS account receiving this environment's resources |
| `AWS_REGION` | Environment secret | Region, defaulting to `us-east-1` |
| `OPENAI_API_KEY` | Environment secret | Raw OpenAI API key |
| `OPENAI_MODEL` | Environment variable, optional | Defaults to `gpt-6.1-sol` |

`BIGDATA_SECRET_ARN` is not used by the current paper replication workflow.

Terraform provisions `jimmy-<env>-openai-api-key` in Secrets Manager from the GitHub
secret. It uses an ephemeral sensitive input and the provider's write-only argument,
so the key is absent from Terraform state and saved plans. The GitHub run number
updates the secret version on each deployment. Foundation outputs contain only the
secret ARN, which workflow Terraform passes to AgentCore automatically. No manual
secret creation, ARN lookup, or `OPENAI_API_KEY_SECRET_ARN` GitHub setting is needed.
Terraform 1.11+ and AWS provider 6.71+ are required; Actions uses Terraform 1.15.7.

The deployment role can write the OpenAI secret and inspect metadata, but is explicitly
denied reading secret values. Runtime roles have scoped read access. Application roles
must use the bootstrap permissions boundary, which limits their maximum permissions
to image pulls, logging, workflow invocation, and the environment's OpenAI secret.
The pipeline cannot modify the bootstrap role or its boundary policy. It can delete
only temporary Terraform lock objects; published web assets are retained, and
resource destruction is outside the deployment role's permissions. Lambda image
access is granted through its execution role, without changing ECR repository policies.

Configurable names follow `jimmy-<env>-<purpose>`. S3 buckets and Cognito domains append
the account ID for uniqueness. AgentCore runtime names use `jimmy_<env>_<workflow>`
because AWS accepts underscores and rejects hyphens; AWS-controlled identifiers and
log namespaces retain their required formats.

Protect `dev` and `prod` to require reviewed PRs and passing CI. Restrict each GitHub
environment to its matching branch. The actual Actions trigger is a branch push,
so branch protection is what enforces merging as the only path to deployment.

## Job ownership

All deployment commands live in `.github/workflows/deploy.yml` and reusable actions.
Terraform defines resources; `workflows.json` is the registry shared by CI and deployment.

| Job | Owns | State key |
| --- | --- | --- |
| `provision-infrastructure` | State bootstrap, gateway, hosting, log encryption and each workflow's ECR repositories | `foundation/terraform.tfstate` |
| `provision-auth` | Cognito pool, client, domain and JWT authorizer | `auth/terraform.tfstate` |
| `provision-workflows` matrix | One named job per registered workflow: API image/Lambda, runtime image/AgentCore, routes, roles and logs | `workflows/<id>/terraform.tfstate` |
| `provision-frontend` | Web build, environment configuration, publication and per-workflow smoke checks | None |

Each workflow owns a separate API Lambda and AgentCore runtime. There is no common
API Lambda, central route registry in Python, or per-workflow Terraform root to copy.
`infra/workflows` calls `infra/modules/workflow` once per isolated state. Matrix jobs
run in parallel after shared infrastructure and auth, and frontend waits for all to succeed.
All applies use state locking. Same-branch deployments queue rather than cancelling applies.

The catalog action validates services before any infrastructure provisioning. Foundation
outputs include namespaced API/runtime repositories and workflow metadata. The config
action rejects cross-environment artifacts and selects the requested workflow. Image
builds reject unregistered IDs and reuse existing immutable commit tags on retries.
A workflow only receives access to the OpenAI key secret when `requires_openai` is true.

No runtime connection artifacts need to pass through a central API job: each module
wires its own API role and runtime ARN. Published frontend config includes shared
Cognito settings, the gateway URL and registry metadata. The smoke step checks every
workflow's public health and rejects anonymous access to its `/me` endpoint.

AgentCore continues to use IAM/SigV4 inbound authentication. API Gateway routes to a
workflow-owned Lambda adapter; that adapter verifies the user and signs runtime
requests. PUBLIC runtime networking allows outbound data acquisition and does not
allow anonymous invocation. Shared HTTP helpers do not contain domain logic.

The state bucket remains `jimmy-<environment>-state-<account>`. The workflow step
creates/configures it before Terraform initializes. OIDC deployment roles must allow
passing only the environment's workflow API/runtime roles. IAM role and function names
include the workflow ID. No database or durable research storage is introduced.

This refactor has not deployed resources. If the earlier split/central API stacks have
been deployed elsewhere, migrate or retire their state before the first apply of the
new layout. The previous `infra/api`, `infra/paper_replication` and central `services/api`
are replaced; resource names and paper API URLs have changed. Do not apply the new
layout on top of old states without planning that migration.

## First-deployment verification

After merging to `dev`, invite a test user to the new Cognito pool using your normal
administrative process. Verify login, sign-out, expired-token handling, PDF submission,
progress, report download and cancellation. Also verify a second user's token cannot
read the first user's run. These cloud checks are not replaced by offline tests.

The persistent data layer is deliberately absent. Do not treat this release as durable
research storage; session expiry removes run files and reports.

## Initial bucket recovery

The first dev bucket creation failed during Terraform's metadata refresh, leaving the
existing bucket tainted. The recovery action checks the known state lineage and
serial, exact bucket identity, account ownership, and Jimmy dev tags before clearing
that one taint with Terraform's state lock. It skips other state snapshots and
healthy resources, and runs during the next merge-triggered deployment. It does not
delete or recreate the bucket. The bucket also has `prevent_destroy` enabled so later
replacement plans stop for review. No `s3:DeleteBucket` permission is granted.

The OpenAI secret version changes on each deployment because the write-only version
counter uses the GitHub run number. That creates a new value version inside the same
Secrets Manager secret; it does not replace the secret or change its ARN.

## API Gateway stage tags

The system `$default` stage uses a separate AWS provider configuration with no
default tags. Its parent API retains the Jimmy environment tags. Tagged stage
creation failed with a request for `apigateway:TagResource`, while AWS Access
Analyzer rejects that literal action as invalid and the official authorization
reference maps tagging to HTTP-verb permissions already on the deployment role.
Creating the system stage without inline tags avoids that inconsistent path without
adding an undocumented IAM action. No stage or API deletion permission is added.

The Cognito provisioning role also needs `cognito-idp:GetUserPoolMfaConfig`: the
provider reads MFA configuration even when MFA is off. This read is restricted to
user pools tagged for Jimmy and the matching environment.
