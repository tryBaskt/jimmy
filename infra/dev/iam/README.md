# Dev deployment permissions

Use only the existing `jimmy-dev-github-actions` role for dev infrastructure.
There is no separate deployment role or IAM workflow. The deployment role cannot
grant itself permissions. Application provisioning creates a separate Lambda
runtime role with logging-only permissions, as documented below.

The version-controlled inline policies are:

| File | Inline policy name | Scope |
|---|---|---|
| `dynamodb-provisioning.json` | `jimmy-dev-dynamodb-provisioning` | Lifecycle, describe, and tagging for the exact dev metadata and universe-membership tables |
| `fmp-secret-provisioning.json` | `jimmy-dev-fmp-secret-provisioning` | Describe/update/tag metadata for the exact imported FMP secret; no secret-value reads |
| `terraform-state.json` | `jimmy-dev-terraform-state` | Bootstrap/configure the dedicated state bucket and read/write the exact state and lock objects |

The DynamoDB policy grants no item reads/writes, IAM, prod, or other table access.
The state policy grants no bucket deletion, state-object deletion, IAM, prod, or
other bucket access. Only the lock object can be deleted. Bucket-level bootstrap
permissions are needed because the Actions job creates/configures its own state
bucket before Terraform initializes its backend.

The original database/state policy documents were attached through an authenticated
administrator session and verified against these files. Subsequent resource creation, including
the state bucket and DynamoDB table, is performed by GitHub Actions. Future policy
changes require an administrator and review against these files.

The `Dev infrastructure` workflow's **provisioning infra** job uses this role
through OIDC on pushes to `dev`, or manual runs from `dev`. The existing GitHub
`dev` environment must allow only the `dev` branch and no tags. No additional
GitHub environment is required.

CI checks workflow syntax, all policy JSON files, and dev Terraform on PRs to
`dev` and `main` without AWS credentials. It never applies infrastructure.

## Application deployment permissions

The application jobs also use `jimmy-dev-github-actions`. Update the existing
`jimmy-dev-terraform-state` policy from `terraform-state.json` for the exact
`dev/backend/terraform.tfstate` and `dev/frontend/terraform.tfstate` state objects
and their lock objects. Add `application-provisioning.json` as the inline policy
`jimmy-dev-application-provisioning` to this same role.

This adds Lambda, logs, API Gateway, the frontend S3 bucket, CloudFront, and the
single log-only runtime role `jimmy-dev-backend-execution`. IAM PassRole is limited
to that role and Lambda. Role creation requires the AWS-managed logging boundary;
the deployment role has no permission to remove it or attach arbitrary managed
policies. It cannot edit its own IAM permissions.

API Gateway API IDs and CloudFront distribution/OAC IDs are allocated by AWS, so
those management statements use ID wildcards in us-east-1 or this account. API
Gateway management is broader than a name prefix because ARNs contain generated
IDs, not names. All bucket/function/role names are exact. CloudFront OAC creation
and log-group discovery require `Resource: "*"`. These are deployment permissions,
not permissions granted to the running backend.

From an authenticated administrator session, apply the reviewed policies:

```sh
aws iam put-role-policy --role-name jimmy-dev-github-actions \
  --policy-name jimmy-dev-terraform-state \
  --policy-document file://infra/dev/iam/terraform-state.json
aws iam put-role-policy --role-name jimmy-dev-github-actions \
  --policy-name jimmy-dev-application-provisioning \
  --policy-document file://infra/dev/iam/application-provisioning.json
```

These commands are setup instructions; no policy changes or deployments have
been executed as part of writing this infrastructure. Application provisioning needs the narrowly scoped runtime-role permissions
above. CI validates all four policy documents and three Terraform roots.
