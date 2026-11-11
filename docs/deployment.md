# GitHub deployment setup

Application deployment is triggered only by a push to `dev` or `prod`. Use branch
protection to require a reviewed PR and successful CI, and restrict each GitHub
environment to its matching branch. The workflow has no `workflow_dispatch` trigger.
It does not deploy from pull requests or feature branches.

## Merge to deploy

The dev bootstrap role `jimmy-dev-github-deploy` trusts the
GitHub OIDC provider for `tryBaskt/jimmy` and the `dev` environment. Its ARN is derived
from the environment's `AWS_ACCOUNT_ID` secret and branch name; no `AWS_ROLE_ARN`
setting is needed. The bootstrap role and workflow permissions boundary are defined
in `infra/github/` and are intentionally outside the application deployment pipeline.
Dev has the ECS bootstrap permissions. Apply the
[bootstrap configuration](../infra/github/README.md) for other environments before deploying there. The GitHub OIDC provider is shared account infrastructure.

The existing GitHub environment secrets are used directly:

| Setting | Kind | Value |
| --- | --- | --- |
| `AWS_ACCOUNT_ID` | Environment secret | AWS account receiving this environment's resources |
| `AWS_REGION` | Environment secret | Region, defaulting to `us-east-1` |
| `OPENAI_API_KEY` | Environment secret | Raw OpenAI API key |
| `OPENAI_MODEL` | Environment variable, optional | Defaults to `gpt-6.1-sol` |
| `RESEARCH_ASSISTANT_MODEL` | Environment variable, optional | Defaults to `gpt-5-mini` |
| `PI_MAX_MODEL_CALLS` | Environment variable, optional | Defaults to 48 |

`BIGDATA_SECRET_ARN` is not used by the current paper replication workflow.

Terraform provisions `jimmy-<env>-openai-api-key` in Secrets Manager from the GitHub
secret. It uses an ephemeral sensitive input and the provider's write-only argument,
so the key is absent from Terraform state and saved plans. The GitHub run number
updates the secret version on each deployment. Foundation outputs contain only the
secret ARN, which workflow Terraform passes to the ECS task automatically. No manual
secret creation, ARN lookup, or `OPENAI_API_KEY_SECRET_ARN` GitHub setting is needed.
Terraform 1.11+ and AWS provider 6.71+ are required; Actions uses Terraform 1.15.7.

The deployment role can write the OpenAI secret and inspect metadata, but is explicitly
denied reading secret values. Application roles use the bootstrap permissions
boundary. The API role can start and stop tasks in its workflow cluster and access
its runs bucket. The task execution role pulls the image and writes logs. The task
role reads its input and the configured OpenAI secret, and writes run outputs.

Configurable names follow `jimmy-<env>-<purpose>`. S3 bucket names append the account
ID for uniqueness.

Protect `dev` and `prod` to require reviewed PRs and passing CI. Restrict each GitHub
environment to its matching branch. The actual Actions trigger is a branch push,
so branch protection is what enforces merging as the only path to deployment.

## Job ownership

Deployment orchestration lives in `.github/workflows/deploy.yml`,
`.github/workflows/deploy-workflow.yml`, and reusable actions.
Terraform defines resources; `workflows.json` is the registry shared by CI and deployment.

| Job | Owns | State key |
| --- | --- | --- |
| `provision-infrastructure` | State bootstrap, gateway, hosting, log encryption and each workflow's ECR repositories | `foundation/terraform.tfstate` |
| `provision-auth` | Cognito pool, client, domain and JWT authorizer | `auth/terraform.tfstate` |
| `provision-workflows` matrix → `publish-images` | Build and publish the selected workflow's API and runtime images | None |
| `provision-workflows` matrix → `provision-workflow` | Provision its Lambda, Fargate task definition, cluster, private runs bucket, routes, roles and logs after publishing succeeds | `workflows/<id>/terraform.tfstate` |
| `provision-frontend` | Web build, environment configuration, publication and per-workflow smoke checks | None |

Each workflow owns a separate API Lambda and ECS worker. There is no common
API Lambda, central route registry in Python, or per-workflow Terraform root to copy.
`infra/workflows` calls `infra/modules/workflow` once per isolated state. Matrix jobs
call `.github/workflows/deploy-workflow.yml` after shared infrastructure and auth.
Each invocation has its own publish → provision dependency, so a failed image build
blocks only that workflow's provisioning. Frontend waits for all workflows to succeed.
Provisioning checks both images in ECR and derives their URIs from foundation metadata
and the immutable commit tag; no image URI passes through cross-job outputs.
All applies use state locking. Same-branch deployments queue rather than cancelling applies.

The catalog action validates services before any infrastructure provisioning. Foundation
outputs include namespaced API/runtime repositories and workflow metadata. The config
action rejects cross-environment artifacts and selects the requested workflow. Image
builds reject unregistered IDs and reuse existing immutable commit tags on retries.
A workflow only receives access to the OpenAI key secret when `requires_openai` is true.

No runtime connection artifacts need to pass through a central API job: each module
wires its API role to its task definition and cluster. Published frontend config includes shared
Cognito settings, the gateway URL and registry metadata. The smoke step checks every
workflow's public health and rejects anonymous access to its `/me` endpoint.

The API submits one Fargate task per run. A task exits once its final snapshot and
artifacts are saved. The task's subnet provides outbound internet access and its
security group has no inbound rules. There is no ECS service or idle task.

The Terraform state bucket stays separate from the workflow's S3 runs bucket.
The migration drops the old AgentCore resources from Terraform state without
deleting them. Existing AgentCore session files do not transfer to S3. Retire the
old resources separately after verifying the ECS cutover.

## First-deployment verification

After merging to `dev`, use **Create account** to verify signup and email confirmation.
Verify login, sign-out, expired-token handling, PDF submission,
progress, report download and cancellation. Also verify a second user's token cannot
read the first user's run. These cloud checks are not replaced by offline tests.

Verify that the report remains downloadable after the ECS task exits.

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

## Terraform state bucket configuration

Each Terraform job derives `STATE_BUCKET` directly from the branch and the GitHub
environment's `AWS_ACCOUNT_ID` secret. Do not pass this value through job outputs:
GitHub suppresses outputs containing secrets, including bucket names containing the
secret account ID, which otherwise leaves downstream backend configuration empty.
The jobs validate the bucket name before initialization. Foundation/auth resource
metadata continues to pass through the existing JSON artifacts.

## Lambda image access

Foundation provisions an ECR image retrieval policy for each workflow API repository.
It grants the Lambda service only image reads, restricted to that workflow's exact
function ARN. The bootstrap deployment role can set repository policies only on
`jimmy-<env>-*-api` repositories; runtime and other-environment repositories are excluded.
