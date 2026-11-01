# GitHub deployment bootstrap

This configuration records the one-time deployment role, its permissions policy, and
the fixed permissions boundary required on application roles. Dev is already
provisioned in account `499133675835`; the application pipeline derives its role ARN
from `AWS_ACCOUNT_ID` and never modifies these bootstrap resources.

Trust is restricted to `tryBaskt/jimmy` using its verified immutable repository/owner
IDs and the matching GitHub environment. Permission templates scope named resources
to `jimmy-<env>-<purpose>`, bound application IAM roles, and deny secret-value reads.
Creation operations and generated-ID resources use tags or regional/account scopes
where AWS cannot authorize by the application's chosen name.

These resources were created through the AWS connection from these policy templates.
For later Terraform administration, import them into a separately retained bootstrap
state before applying this root. For dev, import IDs are:

- `aws_iam_role.github_deploy`: `jimmy-dev-github-deploy`
- `aws_iam_role_policy.github_deploy`: `jimmy-dev-github-deploy:jimmy-dev-github-deploy-permissions`
- `aws_iam_policy.workflow_boundary`: `arn:aws:iam::499133675835:policy/jimmy-dev-workflow-permissions-boundary`

The shared OIDC provider is retained account infrastructure. This root does not
create or delete it. Prod must be bootstrapped separately before its first deployment.
