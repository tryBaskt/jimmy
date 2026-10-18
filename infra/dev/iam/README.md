# Dev deployment permissions

Use only the existing `jimmy-dev-github-actions` role for dev infrastructure.
There is no separate IAM role or IAM workflow. The role has no IAM permissions
and cannot grant itself permissions.

The version-controlled inline policies are:

| File | Inline policy name | Scope |
|---|---|---|
| `dynamodb-provisioning.json` | `jimmy-dev-dynamodb-provisioning` | Lifecycle, describe, and tagging for the exact dev metadata table |
| `terraform-state.json` | `jimmy-dev-terraform-state` | Bootstrap/configure the dedicated state bucket and read/write the exact state and lock objects |

The DynamoDB policy grants no item reads/writes, IAM, prod, or other table access.
The state policy grants no bucket deletion, state-object deletion, IAM, prod, or
other bucket access. Only the lock object can be deleted. Bucket-level bootstrap
permissions are needed because the Actions job creates/configures its own state
bucket before Terraform initializes its backend.

Both policy documents are attached once through the authenticated administrator
session and verified against these files. Subsequent resource creation, including
the state bucket and DynamoDB table, is performed by GitHub Actions. Future policy
changes require an administrator and review against these files.

The `Dev infrastructure` workflow's **provisioning infra** job uses this role
through OIDC on pushes to `dev`, or manual runs from `dev`. The existing GitHub
`dev` environment must allow only the `dev` branch and no tags. No additional
GitHub environment is required.

CI checks workflow syntax, both policy JSON files, and dev Terraform on PRs to
`dev` and `main` without AWS credentials. It never applies infrastructure.
