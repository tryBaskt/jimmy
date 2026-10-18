# Dev infrastructure

This Terraform root manages dev resources in AWS account `499133675835`, region
`us-east-1`. Resource names follow `jimmy-<env>-<purpose>`.

## Stock metadata table

- Name: `jimmy-dev-stock-metadata`
- Partition key: `symbol` (String), containing the FMP symbol
- Capacity: on-demand (`PAY_PER_REQUEST`)
- No sort key, secondary indexes, TTL, or initial items
- Encryption at rest uses DynamoDB's default AWS-owned key

Only key attributes are declared in Terraform. The ingestion job will write
`companyName`, `exchange`, `country`, `state`, `city`, `sector`, `industry`,
`currency`, `marketCap`, `isActivelyTrading`, `universes`, and `updatedAt` as
ordinary item attributes. These represent current metadata, not historical
index membership. Missing FMP values can be omitted.

The table name and ARN are exposed as Terraform outputs for future batch jobs.
This change does not implement ingestion or provision any prod resources.

## Validation and deployment

Run `terraform fmt -check` and `terraform init -backend=false`, followed by
`terraform validate`, from this directory to check the configuration without
deploying resources.

Deployment must run through GitHub Actions. Remote state and the Actions
deployment permissions must be configured before the first apply; this root
does not yet configure a remote backend or an apply workflow. Do not apply
locally or commit Terraform state or plan files.
