# Jimmy

AI-native quantitative research platform. Application implementation has not started.

## Development environment

- AWS account: `499133675835`
- AWS region: `us-east-1`
- Environment: `dev`
- AWS resource names: `jimmy-<env>-<purpose>`
- GitHub Actions role: `jimmy-dev-github-actions`

## GitHub Actions

- **CI** validates the workflow files with checksum-verified Actionlint on pull
  requests to `main`. It is also reusable by the dev workflow and can run manually.
- **Dev AWS connection** runs on pushes to `main` or manually from `main`. It
  requires CI to pass, then verifies OIDC authentication and the AWS account.
- The GitHub environment `dev` permits only the `main` branch, with no allowed
  tags. PR checks have no AWS credentials or OIDC token permission.
- Third-party Actions are pinned to full commit SHAs. There are no stored AWS keys.

This is the CI/CD foundation, not an application deployment. Once hosting is
selected, add build, test, and deployment steps here. All application and
infrastructure deployments must run through GitHub Actions.

## AWS authentication bootstrap

The account-wide GitHub OIDC provider already existed and is reused unchanged.
The project role's trust policy is tracked in
[`infra/iam/github-actions-dev-trust.json`](infra/iam/github-actions-dev-trust.json).
Its subject uses immutable GitHub owner and repository IDs and is restricted to
the `dev` environment. The environment's branch restriction is a required part of
this trust configuration: an environment OIDC subject alone does not limit branches.

The role is initially created without attached permission policies. It can verify
its identity with STS but cannot deploy resources. Add resource-specific deployment
permissions when the hosting design is approved; do not attach administrator access.

The one-time IAM role bootstrap is performed using an existing AWS administrator
session because GitHub Actions cannot assume a role before it exists. Subsequent
deployment changes belong in Actions and version-controlled infrastructure.
