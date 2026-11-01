# Authorized AWS teardown — October 31, 2026

Account: `499133675835`. Regional Jimmy resources were found in `us-east-1`; inventory
checks across all 17 enabled regions found no other regional Jimmy deployments.
Follow-up scans checked named Lambda, ECR, DynamoDB, ECS, Step Functions, SQS, SNS,
EventBridge, VPC, Secrets Manager, CloudWatch alarm/log, KMS alias and REST API inventories.
No new application resources were deployed.

Deleted the old and workspace deployments' APIs, Lambda functions/event mappings,
Cognito pools/domains/users, DynamoDB tables, ECS services/clusters, Step Functions
state machine, EventBridge rule/targets, SQS queues, SNS topic, alarms/log groups,
ECR repositories/images, CloudFront distributions/functions/origin controls/header
policy, VPC endpoints, NAT gateway, elastic IP, subnets, route tables, security groups,
internet gateways, VPCs, application IAM policies/roles, and all seven Jimmy S3 buckets.
Bucket object versions and delete markers were removed, including both environments'
Terraform state. The user explicitly approved data/account deletion after the approval
review requested clarification.

Three Jimmy Secrets Manager secrets were scheduled for deletion with seven-day
recovery windows: `jimmy-dev-fmp-api-key`, `jimmy-dev-bigdata-api-key`, and
`jimmy-dev-alert-email`. Secret values were not retrieved. Deletion leaves those names
reserved during recovery. The new application does not depend on them.

Direct service-list verification returned no matching Jimmy Lambda functions,
Cognito pools, DynamoDB tables, S3 buckets, IAM roles, ECR repositories, HTTP/WebSocket
APIs, ECS clusters/active task definitions or CloudFront distributions. The final
Jimmy VPC was deleted after its managed network interfaces disappeared. Tagging API
results can retain stale deleted-resource records; they were not used as proof that
deleted services were still active.

An unfiltered ECS audit found additional task-definition revisions that the initial
family filter missed. All 20 active Jimmy revisions were deregistered, and all 29
Jimmy revisions were submitted for deletion without failures. Final readback showed
no ACTIVE or INACTIVE Jimmy revisions; all 29 were DELETE_IN_PROGRESS. AWS is still
finalizing those historical definitions; there are no running Jimmy services or tasks.

Unrelated account infrastructure, including the existing Baskt CloudFront site and
shared GitHub OIDC provider, was preserved. The previous deployment role ARNs no
longer work; see [credential setup](deployment.md) before the next branch deployment.
