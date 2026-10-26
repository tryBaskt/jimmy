# Dev deployment permissions

Use only the existing `jimmy-dev-github-actions` role for dev infrastructure.
There is no separate deployment role or IAM workflow. The deployment role cannot
grant itself permissions. Application provisioning creates separate Lambda and
ECS runtime roles, as documented below.

The version-controlled policies are:

| File | Inline policy name | Scope |
|---|---|---|
| `dynamodb-provisioning.json` | `jimmy-dev-dynamodb-provisioning` | Lifecycle, describe, and tagging for the five dev DynamoDB tables |
| `fmp-secret-provisioning.json` | `jimmy-dev-fmp-secret-provisioning` | Describe/update/tag metadata for the exact imported FMP secret; no secret-value reads |
| `terraform-state.json` | `jimmy-dev-terraform-state` | Bootstrap/configure the dedicated state bucket and read/write the exact state and lock objects |
| `application-provisioning.json` | `jimmy-dev-application-provisioning` (customer-managed) | Existing API Lambda, API Gateway, frontend bucket, and CloudFront |
| `platform-provisioning.json` | `jimmy-dev-platform-provisioning` (customer-managed) | Dev auth, worker, queue, result storage, and runtime-role provisioning |

The DynamoDB provisioning policy grants no item reads/writes, IAM, prod, or other table access.
The state policy grants no bucket deletion, state-object deletion, IAM, prod, or
other bucket access. Only the lock object can be deleted. Bucket-level bootstrap
permissions are needed because the Actions job creates/configures its own state
bucket before Terraform initializes its backend.

On 2026-10-22, the five policy documents were applied to
`jimmy-dev-github-actions` in account `499133675835` and verified against these
files. The application and platform policies are customer-managed and attached;
the other three are inline. Subsequent resource creation is performed by GitHub
Actions. Future policy changes require an administrator and review against
these files.

The `Dev infrastructure` workflow's **provisioning infra** job uses this role
through OIDC on pushes to `dev`, or manual runs from `dev`. The existing GitHub
`dev` environment must allow only the `dev` branch and no tags. No additional
GitHub environment is required.

CI checks workflow syntax, all policy JSON files, and dev Terraform on PRs to
`dev` and `main` without AWS credentials. It never applies infrastructure.

## Application deployment permissions

The application jobs also use `jimmy-dev-github-actions`. Update the existing
`jimmy-dev-terraform-state` inline policy from `terraform-state.json` for the
backend, frontend, and worker state/lock objects. Attach
`application-provisioning.json` as a customer-managed policy to this same role.
Use a customer-managed policy for `platform-provisioning.json` too: AWS caps the
sum of a role's inline policies at 10,240 non-whitespace characters, and these
two documents would exceed that limit if installed inline with the existing
state, DynamoDB, and FMP policies. Customer-managed policies have a separate
6,144-character limit per policy; both documents fit that limit.

This adds Lambda, logs, API Gateway, the frontend S3 bucket, CloudFront, and the
runtime role `jimmy-dev-backend-execution`. IAM PassRole is limited to that role
and Lambda in this original policy. The platform policy adds a resource-scoped
backend boundary and worker roles; the deployment role cannot edit its own
IAM permissions.

API Gateway API IDs and CloudFront distribution/OAC IDs are allocated by AWS, so
those management statements use ID wildcards in us-east-1 or this account. API
Gateway management is broader than a name prefix because ARNs contain generated
IDs, not names. All bucket/function/role names are exact. CloudFront OAC creation
and log-group discovery require `Resource: "*"`. These are deployment permissions,
not permissions granted to the running API.

The following commands show the initial administrator bootstrap for another
account or a recreated role. They have already been completed in dev; do not
rerun `create-policy` against an existing policy:

```sh
aws iam put-role-policy --role-name jimmy-dev-github-actions \
  --policy-name jimmy-dev-terraform-state \
  --policy-document file://infra/dev/iam/terraform-state.json
aws iam create-policy --policy-name jimmy-dev-application-provisioning \
  --policy-document file://infra/dev/iam/application-provisioning.json
aws iam attach-role-policy --role-name jimmy-dev-github-actions \
  --policy-arn arn:aws:iam::499133675835:policy/jimmy-dev-application-provisioning
```

Application provisioning needs the narrowly scoped runtime-role permissions
above. CI validates all five policy documents and four Terraform roots. No
application infrastructure was deployed by applying the IAM policies.

## Async strategy platform and sign-in

The dev role now has the revised `dynamodb-provisioning.json` and
`terraform-state.json` policies and the attached customer-managed
`platform-provisioning.json` policy. This policy includes account-wide
resource scopes for specific EC2, Cognito, and ECS provisioning operations
because AWS allocates those resource IDs. Review that scope before attachment.
The deployment role still cannot edit its own permissions. The commands below
record the initial administrator bootstrap and should not be rerun unchanged
in this account:

```sh
aws iam put-role-policy --role-name jimmy-dev-github-actions \
  --policy-name jimmy-dev-dynamodb-provisioning \
  --policy-document file://infra/dev/iam/dynamodb-provisioning.json
aws iam put-role-policy --role-name jimmy-dev-github-actions \
  --policy-name jimmy-dev-terraform-state \
  --policy-document file://infra/dev/iam/terraform-state.json
aws iam create-policy --policy-name jimmy-dev-platform-provisioning \
  --policy-document file://infra/dev/iam/platform-provisioning.json
aws iam attach-role-policy --role-name jimmy-dev-github-actions \
  --policy-arn arn:aws:iam::499133675835:policy/jimmy-dev-platform-provisioning
```

If either managed policy already exists, publish a new default policy version
instead of calling `create-policy`. After verifying the managed application
policy is attached, delete any old inline `jimmy-dev-application-provisioning`
policy to avoid duplicate permissions and conserve the inline-policy quota.

The API role can manage only sessions and jobs, send strategy messages, and
read results. The Fargate worker has separate execution and task roles; its
task role reads universe membership, consumes jobs, writes reports, and invokes
Bedrock. Refresh tokens live only in the server-side sessions table.
