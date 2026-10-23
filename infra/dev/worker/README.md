# Dev strategy worker

The dev workflow builds `workers/strategy_worker/Dockerfile`, pushes an immutable
image to `jimmy-dev-strategy-worker` in ECR, then applies this Terraform root
with that image digest. State is `dev/worker/terraform.tfstate`.

One Fargate task long-polls SQS. It has no inbound rules and uses an assigned
public IP for outbound HTTPS to Yahoo Finance, Bedrock, and AWS services. This
keeps the dev network simple; a private-subnet/NAT or VPC-endpoint design can
replace it later. The ECS service runs continuously, even with an empty queue.
Failed jobs are retried up to three times and then enter the DLQ. The DLQ
CloudWatch alarm has no notification target yet; inspect it in CloudWatch.
