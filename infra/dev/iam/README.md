# Dev IAM

`dev-iam.yml` installs `dynamodb-provisioning.json` as inline policy
`jimmy-dev-dynamodb-provisioning` on `jimmy-dev-github-actions`. It runs after
relevant changes reach `dev`, or manually from `dev`. PRs never obtain AWS
credentials. The existing `dev.yml` now verifies AWS access from `dev` as well.

The policy grants table lifecycle, describe, and tagging operations on exactly
`arn:aws:dynamodb:us-east-1:499133675835:table/jimmy-dev-stock-metadata`.
It grants no item reads/writes, IAM operations, prod access, or other table access.
The describe calls include Terraform's TTL and continuous-backup read checks;
changing TTL/backups, restore/import, streams, and autoscaling is outside this scope.

## Roles and bootstrap

The separate Terraform root `bootstrap/` creates:

- `jimmy-dev-iam`: OIDC role for the `dev-iam` GitHub environment. It can read and
  write inline policies only on `jimmy-dev-github-actions`, and install only the
  approved permissions boundary. It cannot change either role's trust policy,
  change its own permissions, remove the boundary, edit managed policies, or
  provision DynamoDB itself.
- Policy writes are also IAM-condition guarded: `PutRolePolicy` is allowed only
  when the target role already has `jimmy-dev-deployment-boundary` attached.
- `jimmy-dev-deployment-boundary`: maximum allowed permissions for the deployment
  role. Initially it matches the DynamoDB provisioning policy. The IAM workflow
  installs this boundary before granting any policy permissions.

This root uses the existing GitHub OIDC provider and deployment role. It does not
recreate them. Keep bootstrap state separate from `infra/dev` deployment state.

Before the IAM workflow can run, an administrator must bootstrap this root using
an approved administrator deployment process and persistent, separate state.
The workflow cannot create the role it needs to authenticate. Do not give the
ordinary deployment role IAM administration permissions to bypass this step.
Normal resource deployments and ongoing deployment-policy changes run through
GitHub Actions; this bootstrap is the one-time prerequisite.

Before bootstrap, configure GitHub environment `dev-iam` to allow only the `dev`
branch, with a required administrator reviewer and no allowed tags. Change the
existing `dev` environment's allowed branch from `main` to `dev`. These environment
rules are required: environment OIDC subjects do not themselves restrict branches.
Do not dispatch or enable deployment runs before those protections are configured.

Future permissions beyond the boundary need a separately reviewed administrator
bootstrap update first. The IAM workflow cannot expand the boundary. The current
boundary contains only DynamoDB operations; Terraform remote-state permissions
must also be designed and added before resource deployment is enabled.

## Validation

CI validates workflow syntax, JSON, and both Terraform roots without AWS credentials
on PRs to `dev` and `main`. No apply or bootstrap runs occur on pull requests.
Run formatting and validation from each root with `terraform init -backend=false`
and `terraform validate`. Commit both provider lock files.
