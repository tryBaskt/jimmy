resource "aws_s3_bucket" "runs" {
  bucket = "${local.prefix}-runs-${var.foundation.account}"
}

resource "aws_s3_bucket_public_access_block" "runs" {
  bucket                  = aws_s3_bucket.runs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "runs" {
  bucket = aws_s3_bucket.runs.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_vpc" "worker" {
  cidr_block           = "10.42.0.0/24"
  enable_dns_hostnames = true
  tags                 = { Name = "${local.prefix}-worker" }
}

resource "aws_subnet" "worker" {
  vpc_id     = aws_vpc.worker.id
  cidr_block = "10.42.0.0/24"
  tags       = { Name = "${local.prefix}-worker" }
}

data "aws_availability_zones" "worker" {
  state = "available"
  filter {
    name   = "zone-type"
    values = ["availability-zone"]
  }
}

locals {
  # AWS Fargate does not support ARM64 in this physical zone. Zone names vary by account.
  arm_worker_zones = sort([for zone in data.aws_availability_zones.worker.zone_ids : zone if zone != "use1-az3"])
  worker_zones     = slice(local.arm_worker_zones, 0, min(2, length(local.arm_worker_zones)))
}

resource "aws_vpc_ipv4_cidr_block_association" "worker_capacity" {
  vpc_id = aws_vpc.worker.id
  # The original subnet occupies the whole primary CIDR. Add address space in place.
  cidr_block = "10.42.1.0/24"
  lifecycle {
    precondition {
      condition     = length(local.worker_zones) == 2
      error_message = "The Fargate worker needs two ARM64-supported Availability Zones."
    }
  }
}

resource "aws_subnet" "worker_capacity" {
  for_each = { for index, zone in local.worker_zones : zone => index }

  vpc_id               = aws_vpc.worker.id
  availability_zone_id = each.key
  cidr_block           = cidrsubnet(aws_vpc_ipv4_cidr_block_association.worker_capacity.cidr_block, 1, each.value)
  tags                 = { Name = "${local.prefix}-worker-${each.key}" }

  depends_on = [aws_vpc_ipv4_cidr_block_association.worker_capacity]
}

resource "aws_internet_gateway" "worker" {
  vpc_id = aws_vpc.worker.id
  tags   = { Name = "${local.prefix}-worker" }
}

resource "aws_route_table" "worker" {
  vpc_id = aws_vpc.worker.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.worker.id
  }
  tags = { Name = "${local.prefix}-worker" }
}

resource "aws_route_table_association" "worker" {
  subnet_id      = aws_subnet.worker.id
  route_table_id = aws_route_table.worker.id
}

resource "aws_route_table_association" "worker_capacity" {
  for_each = aws_subnet.worker_capacity

  subnet_id      = each.value.id
  route_table_id = aws_route_table.worker.id
}

resource "aws_security_group" "worker" {
  name    = "${local.prefix}-worker"
  vpc_id  = aws_vpc.worker.id
  ingress = []
  # Fargate needs outbound HTTPS for ECR, AWS APIs, market data and OpenAI.
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_cluster" "worker" {
  name = "${local.prefix}-worker"
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/ecs/${local.prefix}-worker"
  retention_in_days = var.foundation.environment == "prod" ? 90 : 14
  kms_key_id        = var.foundation.logs_key_arn
}

locals {
  boundary_arn = "arn:aws:iam::${var.foundation.account}:policy/jimmy-${var.foundation.environment}-workflow-permissions-boundary"
  ecs_trust = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = {
      StringEquals = { "aws:SourceAccount" = var.foundation.account },
      ArnLike      = { "aws:SourceArn" = "${local.ecs_arn}:*" }
    }
  }] })
}

resource "aws_iam_role" "execution" {
  name                 = "${local.prefix}-execution"
  permissions_boundary = local.boundary_arn
  assume_role_policy   = local.ecs_trust
}

resource "aws_iam_role_policy" "execution" {
  name = "${local.prefix}-execution-permissions"
  role = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = "arn:aws:ecr:${var.foundation.region}:${var.foundation.account}:repository/${local.prefix}-runtime" },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.worker.arn}:*" }
  ] })
}

resource "aws_iam_role" "worker" {
  name                 = "${local.prefix}-worker"
  permissions_boundary = local.boundary_arn
  assume_role_policy   = local.ecs_trust
}

resource "aws_iam_role_policy" "worker" {
  name = "${local.prefix}-worker-permissions"
  role = aws_iam_role.worker.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.runs.arn },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = [for path in ["request.json", "uploads/*"] : "${aws_s3_bucket.runs.arn}/runs/*/*/${path}"] },
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = [for path in ["snapshot.json", "artifacts/*"] : "${aws_s3_bucket.runs.arn}/runs/*/*/${path}"] }
    ], var.workflow.requires_openai ? [
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = var.openai_api_key_secret_arn }
  ] : []) })
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.prefix}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 2048
  memory                   = 8192
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.worker.arn
  # A pending launch can retry with its original revision after a deployment.
  skip_destroy = true
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }
  container_definitions = jsonencode([{
    name      = "worker"
    image     = var.runtime_image
    essential = true
    environment = [for name, value in merge({ RUN_BUCKET = aws_s3_bucket.runs.bucket }, var.workflow.requires_openai ? {
      OPENAI_MODEL              = var.openai_model
      RESEARCH_ASSISTANT_MODEL  = var.research_assistant_model
      PI_MAX_MODEL_CALLS        = tostring(var.pi_max_model_calls)
      OPENAI_API_KEY_SECRET_ARN = var.openai_api_key_secret_arn
    } : {}) : { name = name, value = value }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.worker.name
        "awslogs-region"        = var.foundation.region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])
  lifecycle {
    precondition {
      condition     = !var.workflow.requires_openai || var.openai_api_key_secret_arn != ""
      error_message = "This workflow requires an OpenAI API key secret ARN."
    }
  }
  depends_on = [aws_iam_role_policy.execution, aws_iam_role_policy.worker]
}
