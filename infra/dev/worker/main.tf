variable "image_uri" {
  type        = string
  description = "Immutable ECR image URI built by CI, including its digest."
}

resource "aws_vpc" "worker" {
  cidr_block           = "10.73.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "jimmy-dev-strategy-worker" }
}

resource "aws_internet_gateway" "worker" {
  vpc_id = aws_vpc.worker.id
  tags   = { Name = "jimmy-dev-strategy-worker" }
}

resource "aws_subnet" "worker" {
  for_each                = { a = ["us-east-1a", "10.73.1.0/24"], b = ["us-east-1b", "10.73.2.0/24"] }
  vpc_id                  = aws_vpc.worker.id
  availability_zone       = each.value[0]
  cidr_block              = each.value[1]
  map_public_ip_on_launch = false
  tags                    = { Name = "jimmy-dev-strategy-worker-${each.key}" }
}

resource "aws_route_table" "worker" {
  vpc_id = aws_vpc.worker.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.worker.id
  }
  tags = { Name = "jimmy-dev-strategy-worker" }
}

resource "aws_route_table_association" "worker" {
  for_each       = aws_subnet.worker
  subnet_id      = each.value.id
  route_table_id = aws_route_table.worker.id
}

resource "aws_security_group" "worker" {
  name        = "jimmy-dev-strategy-worker"
  description = "Outbound HTTPS only; no inbound connections"
  vpc_id      = aws_vpc.worker.id
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/jimmy-dev-strategy-worker"
  retention_in_days = 14
}

resource "aws_iam_role" "execution" {
  name                 = "jimmy-dev-strategy-worker-execution"
  permissions_boundary = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_policy" "task" {
  name = "jimmy-dev-strategy-worker-runtime"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility"], Resource = "arn:aws:sqs:us-east-1:499133675835:jimmy-dev-strategy-jobs" },
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:UpdateItem"], Resource = "arn:aws:dynamodb:us-east-1:499133675835:table/jimmy-dev-strategy-jobs" },
      { Effect = "Allow", Action = "dynamodb:Query", Resource = "arn:aws:dynamodb:us-east-1:499133675835:table/jimmy-dev-stock-universe-membership" },
      { Effect = "Allow", Action = "s3:PutObject", Resource = "arn:aws:s3:::jimmy-dev-strategy-results-499133675835/jobs/*" },
      {
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = "arn:aws:bedrock:us-east-1:499133675835:inference-profile/us.moonshotai.kimi-k3"
      },
      {
        Effect = "Allow"
        Action = "bedrock:InvokeModel"
        Resource = [
          "arn:aws:bedrock:us-east-1::foundation-model/moonshotai.kimi-k3",
          "arn:aws:bedrock:us-east-2::foundation-model/moonshotai.kimi-k3",
          "arn:aws:bedrock:us-west-2::foundation-model/moonshotai.kimi-k3"
        ]
      }
    ]
  })
}

resource "aws_iam_role" "task" {
  name                 = "jimmy-dev-strategy-worker-task"
  permissions_boundary = aws_iam_policy.task.arn
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "task" {
  role       = aws_iam_role.task.name
  policy_arn = aws_iam_policy.task.arn
}

resource "aws_ecs_cluster" "worker" {
  name = "jimmy-dev-strategy-worker"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "jimmy-dev-strategy-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 2048
  memory                   = 4096
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([{
    name        = "strategy-worker"
    image       = var.image_uri
    essential   = true
    stopTimeout = 120
    environment = [
      { name = "AWS_REGION", value = "us-east-1" },
      { name = "JIMMY_JOBS_TABLE", value = "jimmy-dev-strategy-jobs" },
      { name = "JIMMY_JOBS_QUEUE_URL", value = "https://sqs.us-east-1.amazonaws.com/499133675835/jimmy-dev-strategy-jobs" },
      { name = "JIMMY_RESULTS_BUCKET", value = "jimmy-dev-strategy-results-499133675835" },
      { name = "JIMMY_BEDROCK_MODEL_ID", value = "us.moonshotai.kimi-k3" }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.worker.name
        "awslogs-region"        = "us-east-1"
        "awslogs-stream-prefix" = "worker"
        mode                    = "blocking"
      }
    }
  }])
}

resource "aws_ecs_service" "worker" {
  name             = "jimmy-dev-strategy-worker"
  cluster          = aws_ecs_cluster.worker.id
  task_definition  = aws_ecs_task_definition.worker.arn
  desired_count    = 1
  launch_type      = "FARGATE"
  platform_version = "LATEST"
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = [for subnet in aws_subnet.worker : subnet.id]
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = true
  }
  depends_on = [aws_iam_role_policy_attachment.execution, aws_iam_role_policy_attachment.task]
}

output "worker_cluster" { value = aws_ecs_cluster.worker.name }
output "worker_service" { value = aws_ecs_service.worker.name }
