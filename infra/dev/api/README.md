# Dev API

Independent Terraform root, applied by the **provisioning api** job after
**provisioning infra** has bootstrapped the state bucket.

- Lambda: `jimmy-dev-backend`, Python 3.14, Linux x86_64, 256 MB, 15-second timeout
- API Gateway HTTP API: `jimmy-dev-backend`, default auto-deploy stage
- Log group: `/aws/lambda/jimmy-dev-backend`, 14-day retention
- Execution role: `jimmy-dev-backend-execution`, writes only this function's logs
- State: `dev/backend/terraform.tfstate` in the existing dev state bucket

The Terraform state key, resource addresses, and AWS names keep their original
`backend` values to preserve any resources already deployed. The source and
workflow now use `api`.

CI packages the application with Mangum, then passes the ZIP path through
`TF_VAR_package_path`. Terraform hashes the package to detect code changes.
The workflow verifies `/api/health` and passes `api_url` to frontend provisioning.
The runtime role has no DynamoDB, FMP, or Secrets Manager access yet because the
starter API makes no such calls. Its permissions boundary is the AWS-managed
`AWSLambdaBasicExecutionRole`; the inline policy narrows access to its own logs.
The deployment role can create/pass only this runtime role and cannot remove its
boundary. Deployment still uses `jimmy-dev-github-actions`.

The HTTPS API and documentation are public. Add authentication before adding
private endpoints. No batch jobs run inside this HTTP service.

Validate with `terraform init -backend=false -lockfile=readonly` and
`terraform validate`. Deploy only via GitHub Actions from `dev`.
