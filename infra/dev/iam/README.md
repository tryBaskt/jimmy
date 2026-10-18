# Dev deployment permissions

Use the existing `jimmy-dev-github-actions` role for dev infrastructure. There
is no separate IAM role, IAM workflow, permissions boundary, or IAM Terraform root.

`dynamodb-provisioning.json` is the version-controlled policy to attach directly
to this existing role as inline policy `jimmy-dev-dynamodb-provisioning`.
It grants lifecycle, describe, and tagging operations on exactly
`arn:aws:dynamodb:us-east-1:499133675835:table/jimmy-dev-stock-metadata`.
It grants no item reads/writes, IAM operations, prod access, or other table access.
The describe calls include Terraform's TTL and continuous-backup read checks;
changing TTL/backups, restore/import, streams, and autoscaling is outside this scope.

The policy was attached to the existing role on October 18, 2026 using the
authenticated administrator session, and the installed document was verified
against this file. This was a one-time permissions setup; no new role or boundary
was created. The deployment role has no IAM permissions and cannot grant itself
permissions. Future policy changes require an administrator and should be reviewed
against this file before attachment.

The `dev.yml` workflow authenticates as `jimmy-dev-github-actions` through OIDC and
verifies the account on pushes to `dev`, or manual runs from `dev`. Configure the
existing GitHub `dev` environment to allow only the `dev` branch and no tags;
it currently requires updating from its original `main` restriction. No `dev-iam`
environment or additional administrator-review environment is required.

CI checks workflow syntax, policy JSON, and the dev resource Terraform root on
PRs to `dev` and `main` without AWS credentials. It never applies permissions or
creates resources. The resource apply workflow and remote Terraform state,
including state-access permissions for the existing role, are still pending.
