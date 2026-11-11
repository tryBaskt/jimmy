resource "aws_iam_role" "runtime" {
  permissions_boundary = "arn:aws:iam::${var.foundation.account}:policy/jimmy-${var.foundation.environment}-workflow-permissions-boundary"
  name                 = "${local.prefix}-runtime"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "bedrock-agentcore.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = {
      StringEquals = { "aws:SourceAccount" = var.foundation.account },
      ArnLike      = { "aws:SourceArn" = "arn:aws:bedrock-agentcore:${var.foundation.region}:${var.foundation.account}:runtime/${local.runtime_prefix}*" }
    }
  }] })
}

resource "aws_cloudwatch_log_group" "runtime" {
  name              = "/aws/bedrock-agentcore/runtimes/${aws_bedrockagentcore_agent_runtime.runtime.agent_runtime_id}-DEFAULT"
  retention_in_days = var.foundation.environment == "prod" ? 90 : 14
  kms_key_id        = var.foundation.logs_key_arn
}

resource "aws_iam_role_policy" "runtime" {
  name = "${local.prefix}-runtime-permissions"
  role = aws_iam_role.runtime.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], Resource = "arn:aws:ecr:${var.foundation.region}:${var.foundation.account}:repository/${local.prefix}-runtime" },
    { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:DescribeLogStreams", "logs:CreateLogStream", "logs:PutLogEvents"], Resource = "arn:aws:logs:${var.foundation.region}:${var.foundation.account}:log-group:/aws/bedrock-agentcore/runtimes/${local.runtime_prefix}*:*" }
  ] })
}

resource "aws_bedrockagentcore_agent_runtime" "runtime" {
  agent_runtime_name = local.runtime_prefix
  role_arn           = aws_iam_role.runtime.arn
  agent_runtime_artifact {
    container_configuration { container_uri = var.runtime_image }
  }
  protocol_configuration { server_protocol = "HTTP" }
  # IAM/SigV4 inbound auth is the default; only the API role can invoke it.
  # PUBLIC provides outbound internet for Yahoo; it does not disable IAM authentication.
  network_configuration { network_mode = "PUBLIC" }
  environment_variables = merge({ JIMMY_WORKSPACE = "/tmp/jimmy" }, var.workflow.requires_openai ? {
    OPENAI_MODEL              = var.openai_model
    RESEARCH_ASSISTANT_MODEL  = var.research_assistant_model
    OPENAI_API_KEY_SECRET_ARN = var.openai_api_key_secret_arn
  } : {})
  lifecycle_configuration {
    idle_runtime_session_timeout = 900
    max_lifetime                 = 28800
  }
  lifecycle {
    precondition {
      condition     = !var.workflow.requires_openai || var.openai_api_key_secret_arn != ""
      error_message = "This workflow requires an OpenAI API key secret ARN."
    }
  }
  depends_on = [aws_iam_role_policy.runtime, aws_iam_role_policy.openai_secret]
}


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
    { Effect = "Allow", Action = ["bedrock-agentcore:InvokeAgentRuntime"], Resource = [aws_bedrockagentcore_agent_runtime.runtime.agent_runtime_arn, "${aws_bedrockagentcore_agent_runtime.runtime.agent_runtime_arn}/runtime-endpoint/*"] },
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
      JIMMY_ENV             = var.foundation.environment
      AGENTCORE_RUNTIME_ARN = aws_bedrockagentcore_agent_runtime.runtime.agent_runtime_arn
      COGNITO_ISSUER        = var.auth.cognito_issuer
      COGNITO_CLIENT_ID     = var.auth.cognito_client_id
      OPENAI_MODEL          = var.openai_model
      WEB_ORIGINS           = var.foundation.web_origin
    }
  }
  depends_on = [aws_iam_role_policy.api]
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
  value = { id = var.workflow.id, api_prefix = var.workflow.api_prefix, runtime_arn = aws_bedrockagentcore_agent_runtime.runtime.agent_runtime_arn }
}

resource "aws_iam_role_policy" "openai_secret" {
  name  = "${local.prefix}-openai-secret-read"
  count = var.workflow.requires_openai ? 1 : 0
  role  = aws_iam_role.runtime.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = var.openai_api_key_secret_arn
  }] })
}
