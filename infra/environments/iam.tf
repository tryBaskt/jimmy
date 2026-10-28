locals {

  services = toset(["projects", "research", "data", "artifacts", "activity", "identity"])
  lambdas  = toset(concat(tolist(local.services), ["connections", "publisher", "reconciler"]))
  lambda_trust = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "lambda.amazonaws.com"
      }, Action = "sts:AssumeRole"
    }]
  })
  ecs_trust = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "ecs-tasks.amazonaws.com"
        }, Action = "sts:AssumeRole", Condition = {
        StringEquals = {
          "aws:SourceAccount" = local.account
          }, ArnLike = {
          "aws:SourceArn" = "arn:aws:ecs:${var.region}:${local.account}:*"
        }
      }
    }]
  })

}
resource "aws_iam_role" "lambda" {

  for_each           = local.lambdas
  name               = "${local.prefix}-${each.key}-role"
  path               = "/${local.prefix}/"
  assume_role_policy = local.lambda_trust

}
resource "aws_iam_role_policy" "lambda" {

  for_each = local.lambdas
  name     = "${local.prefix}-${each.key}-access"
  role     = aws_iam_role.lambda[each.key].id
  policy = jsonencode({
    Version = "2012-10-17", Statement = concat([
      {
        Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.lambda[each.key].arn}:*"
      },
      {
        Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem"], Resource = aws_dynamodb_table.metadata.arn
      }
      ], contains(["projects", "research", "data", "artifacts"], each.key) ? [
      {
        Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${aws_s3_bucket.artifacts.arn}/projects/*", "${aws_s3_bucket.artifacts.arn}/staging/*"]
      }
      ] : [], each.key == "data" ? [
      {
        Effect = "Allow", Action = ["s3:PutObject"], Resource = ["${aws_s3_bucket.artifacts.arn}/projects/*", "${aws_s3_bucket.artifacts.arn}/staging/*"]
      }
      ] : [], each.key == "research" ? [
      {
        Effect = "Allow", Action = ["states:StartExecution"], Resource = aws_sfn_state_machine.research.arn
      },
      {
        Effect = "Allow", Action = ["states:StopExecution"], Resource = "arn:aws:states:${var.region}:${local.account}:execution:${local.prefix}-research:*"
      }
      ] : [], each.key == "reconciler" ? [{ Effect = "Allow", Action = ["states:DescribeExecution"], Resource = "arn:aws:states:${var.region}:${local.account}:execution:${local.prefix}-research:*" }] : [], each.key == "publisher" ? [
      {
        Effect = "Allow", Action = ["dynamodb:DescribeStream", "dynamodb:GetRecords", "dynamodb:GetShardIterator"], Resource = aws_dynamodb_table.metadata.stream_arn
      },
      {
        Effect = "Allow", Action = ["dynamodb:ListStreams"], Resource = "*"
      },
      {
        Effect = "Allow", Action = ["execute-api:ManageConnections"], Resource = "${aws_apigatewayv2_api.live.execution_arn}/*/POST/@connections/*"
      }
    ] : [])
  })

}
resource "aws_iam_role" "execution" {

  name               = "${local.prefix}-task-execution-role"
  path               = "/${local.prefix}/"
  assume_role_policy = local.ecs_trust

}
resource "aws_iam_role_policy" "execution" {

  name = "${local.prefix}-task-execution-access"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = concat([
      {
        Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*"
      },
      {
        Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = "arn:aws:ecr:${var.region}:${local.account}:repository/${local.prefix}-worker"
      },
      {
        Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.worker.arn}:*"
      }
      ], var.bigdata_secret_arn != "" ? [{
        Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = var.bigdata_secret_arn
    }] : [])
  })

}
resource "aws_iam_role" "task" {

  for_each           = toset(["worker", "runner"])
  name               = "${local.prefix}-${each.key}-role"
  path               = "/${local.prefix}/"
  assume_role_policy = local.ecs_trust

}
resource "aws_iam_role_policy" "task" {

  for_each = toset(["worker", "runner"])
  name     = "${local.prefix}-${each.key}-access"
  role     = aws_iam_role.task[each.key].id
  policy = jsonencode({
    Version = "2012-10-17", Statement = concat([
      {
        Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem"], Resource = aws_dynamodb_table.metadata.arn
      },
      {
        Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${aws_s3_bucket.artifacts.arn}/projects/*", "${aws_s3_bucket.artifacts.arn}/shared-data/*"]
      },
      {
        Effect = "Allow", Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.artifacts.arn}/projects/*/runs/*"
      }
      ], each.key == "worker" ? [
      {
        Effect = "Allow", Action = ["s3:PutObject"], Resource = ["${aws_s3_bucket.artifacts.arn}/shared-data/*"]
      },
      {
        Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], Resource = var.bedrock_model_arns
        }, {
        Effect = "Allow", Action = ["textract:StartDocumentAnalysis", "textract:GetDocumentAnalysis"], Resource = ["*"]
      }
    ] : [])
  })

}
resource "aws_iam_role" "workflow" {

  name = "${local.prefix}-workflow-role"
  path = "/${local.prefix}/"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "states.amazonaws.com"
        }, Action = "sts:AssumeRole", Condition = {
        StringEquals = {
          "aws:SourceAccount" = local.account
          }, ArnEquals = {
          "aws:SourceArn" = "arn:aws:states:${var.region}:${local.account}:stateMachine:${local.prefix}-research"
        }
      }
    }]
  })

}
resource "aws_iam_role_policy" "workflow" {

  name = "${local.prefix}-workflow-access"
  role = aws_iam_role.workflow.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      {
        Effect = "Allow", Action = ["ecs:RunTask"], Resource = [for task in aws_ecs_task_definition.task : "${task.arn_without_revision}:*"]
      },
      {
        Effect = "Allow", Action = ["ecs:StopTask", "ecs:DescribeTasks"], Resource = "arn:aws:ecs:${var.region}:${local.account}:task/${aws_ecs_cluster.compute.name}/*"
      },
      {
        Effect = "Allow", Action = ["iam:PassRole"], Resource = concat([aws_iam_role.execution.arn], [for r in aws_iam_role.task : r.arn]), Condition = {
          StringEquals = {
            "iam:PassedToService" = "ecs-tasks.amazonaws.com"
          }
        }
      },
      {
        Effect = "Allow", Action = ["events:PutTargets", "events:PutRule", "events:DescribeRule"], Resource = "arn:aws:events:${var.region}:${local.account}:rule/StepFunctionsGetEventsForECSTaskRule"
      }
    ]
  })

}
