variable "package_path" {
  description = "Path to the Linux x86_64 Python 3.14 Lambda ZIP produced by CI."
  type        = string
}

variable "cognito_domain" {
  type    = string
  default = ""
}
variable "cognito_client_id" {
  type    = string
  default = ""
}
variable "public_url" {
  type    = string
  default = ""
}
variable "jobs_queue_url" {
  type    = string
  default = ""
}

resource "aws_iam_policy" "backend_boundary" {
  name = "jimmy-dev-backend-boundary"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.backend.arn}:*"
      },
      {
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:UpdateItem"]
        Resource = [
          "arn:aws:dynamodb:us-east-1:499133675835:table/jimmy-dev-user-sessions",
          "arn:aws:dynamodb:us-east-1:499133675835:table/jimmy-dev-strategy-jobs"
        ]
      },
      {
        Effect   = "Allow"
        Action   = "sqs:SendMessage"
        Resource = "arn:aws:sqs:us-east-1:499133675835:jimmy-dev-strategy-jobs"
      },
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "arn:aws:s3:::jimmy-dev-strategy-results-499133675835/jobs/*"
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "backend" {
  name              = "/aws/lambda/jimmy-dev-backend"
  retention_in_days = 14
}

resource "aws_iam_role" "backend" {
  name                 = "jimmy-dev-backend-execution"
  permissions_boundary = aws_iam_policy.backend_boundary.arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "logs" {
  name = "jimmy-dev-backend-logs"
  role = aws_iam_role.backend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.backend.arn}:*"
    }]
  })
}

resource "aws_iam_role_policy" "application" {
  name   = "jimmy-dev-backend-application"
  role   = aws_iam_role.backend.id
  policy = aws_iam_policy.backend_boundary.policy
}

resource "aws_lambda_function" "backend" {
  function_name    = "jimmy-dev-backend"
  role             = aws_iam_role.backend.arn
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  handler          = "app.lambda_handler.handler"
  runtime          = "python3.14"
  architectures    = ["x86_64"]
  memory_size      = 256
  timeout          = 29
  depends_on       = [aws_iam_role_policy.logs, aws_iam_role_policy.application]
  environment {
    variables = {
      JIMMY_COGNITO_DOMAIN    = var.cognito_domain
      JIMMY_COGNITO_CLIENT_ID = var.cognito_client_id
      JIMMY_PUBLIC_URL        = var.public_url
      JIMMY_SESSIONS_TABLE    = "jimmy-dev-user-sessions"
      JIMMY_JOBS_TABLE        = "jimmy-dev-strategy-jobs"
      JIMMY_JOBS_QUEUE_URL    = var.jobs_queue_url
      JIMMY_RESULTS_BUCKET    = "jimmy-dev-strategy-results-499133675835"
    }
  }
}

resource "aws_apigatewayv2_api" "backend" {
  name          = "jimmy-dev-backend"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "backend" {
  api_id                 = aws_apigatewayv2_api.backend.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.backend.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_route" "backend" {
  api_id    = aws_apigatewayv2_api.backend.id
  route_key = "ANY /api/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.backend.id}"
}

resource "aws_apigatewayv2_stage" "backend" {
  api_id      = aws_apigatewayv2_api.backend.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }
}

resource "aws_lambda_permission" "api" {
  statement_id   = "AllowApiGateway"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.backend.function_name
  principal      = "apigateway.amazonaws.com"
  source_arn     = "${aws_apigatewayv2_api.backend.execution_arn}/*/*/api/*"
  source_account = "499133675835"
}

output "api_url" {
  value = aws_apigatewayv2_api.backend.api_endpoint
}
