resource "aws_iam_role" "api" {
  permissions_boundary = "arn:aws:iam::${var.foundation.account}:policy/jimmy-${var.foundation.environment}-workflow-permissions-boundary"
  name                 = "${local.prefix}-api"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.prefix}-api"
  retention_in_days = var.foundation.environment == "prod" ? 90 : 14
  kms_key_id        = var.foundation.logs_key_arn
}

resource "aws_iam_role_policy" "api" {
  name = "${local.prefix}-api-permissions"
  role = aws_iam_role.api.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecs:RunTask"], Resource = "${local.task_family_arn}:*", Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.worker.arn } } },
    { Effect = "Allow", Action = ["ecs:DescribeTasks", "ecs:StopTask"], Resource = "${local.ecs_arn}:task/${aws_ecs_cluster.worker.name}/*" },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.worker.arn, aws_iam_role.execution.arn], Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } } },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.runs.arn },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.runs.arn}/runs/*" },
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = [for path in ["request.json", "task.json", "cancelled.json", "uploads/*"] : "${aws_s3_bucket.runs.arn}/runs/*/*/${path}"] },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.api.arn}:*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], Resource = "arn:aws:ecr:${var.foundation.region}:${var.foundation.account}:repository/${local.prefix}-api" }
  ] })
}

resource "aws_lambda_function" "api" {
  function_name = "${local.prefix}-api"
  role          = aws_iam_role.api.arn
  package_type  = "Image"
  image_uri     = var.api_image
  architectures = ["arm64"]
  memory_size   = 1024
  timeout       = 29
  environment {
    variables = {
      JIMMY_ENV               = var.foundation.environment
      RUN_BUCKET              = aws_s3_bucket.runs.bucket
      ECS_CLUSTER_ARN         = aws_ecs_cluster.worker.arn
      ECS_TASK_DEFINITION_ARN = aws_ecs_task_definition.worker.arn
      ECS_SUBNET_IDS          = aws_subnet.worker.id
      ECS_SECURITY_GROUP_ID   = aws_security_group.worker.id
      COGNITO_ISSUER          = var.auth.cognito_issuer
      COGNITO_CLIENT_ID       = var.auth.cognito_client_id
      WEB_ORIGINS             = var.foundation.web_origin
    }
  }
  depends_on = [aws_iam_role_policy.api, aws_route_table_association.worker, aws_s3_bucket_public_access_block.runs]
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = var.foundation.api_id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "api" {
  api_id             = var.foundation.api_id
  route_key          = "ANY ${var.workflow.api_prefix}/{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = "JWT"
  authorizer_id      = var.auth.authorizer_id
}

resource "aws_apigatewayv2_route" "preflight" {
  api_id             = var.foundation.api_id
  route_key          = "OPTIONS ${var.workflow.api_prefix}/{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = "NONE"
}

resource "aws_apigatewayv2_route" "health" {
  api_id             = var.foundation.api_id
  route_key          = "GET ${var.workflow.api_prefix}/health"
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "api" {
  statement_id  = "${local.prefix}-gateway-invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "arn:aws:execute-api:${var.foundation.region}:${var.foundation.account}:${var.foundation.api_id}/*/*"
}

output "deployment" {
  value = { id = var.workflow.id, api_prefix = var.workflow.api_prefix, cluster_arn = aws_ecs_cluster.worker.arn, task_definition_arn = aws_ecs_task_definition.worker.arn }
}
