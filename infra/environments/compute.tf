resource "aws_cloudwatch_log_group" "lambda" {

  for_each          = local.lambdas
  name              = "/aws/lambda/${local.prefix}-${each.key}"
  retention_in_days = var.environment == "prod" ? 90 : 14

}
resource "aws_cloudwatch_log_group" "worker" {

  name              = "/ecs/${local.prefix}-experiments"
  retention_in_days = var.environment == "prod" ? 90 : 14

}
resource "aws_ecs_cluster" "compute" {

  name = "${local.prefix}-compute"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }

}
resource "aws_ecs_task_definition" "task" {

  for_each                 = toset(["worker", "runner"])
  family                   = "${local.prefix}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "4096"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task[each.key].arn
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }
  volume {
    name = "tmp"
  }
  container_definitions = jsonencode(local.task_templates[each.key].containerDefinitions)
  lifecycle {
    # Release jobs register desired templates from the deployment contract.
    ignore_changes = [container_definitions]
  }

}
resource "aws_sfn_state_machine" "research" {

  name       = "${local.prefix}-research"
  role_arn   = aws_iam_role.workflow.arn
  type       = "STANDARD"
  definition = jsonencode(local.workflow_definition)
  lifecycle {
    # The workflow release job publishes the desired graph with pinned task ARNs.
    ignore_changes = [definition]
  }
  depends_on = [aws_iam_role_policy.workflow]

}
resource "aws_lambda_function" "api" {

  for_each      = local.services
  function_name = "${local.prefix}-${each.key}"
  role          = aws_iam_role.lambda[each.key].arn
  package_type  = "Image"
  image_uri     = var.api_image
  lifecycle {
    # Infrastructure owns configuration; per-service release jobs own code.
    ignore_changes = [image_uri]
  }
  timeout     = 60
  memory_size = 1024
  environment {
    variables = merge({ for k, v in local.env : k => v if k != "AWS_REGION" }, {
      API_SERVICE = each.key, WORKFLOW_ARN = aws_sfn_state_machine.research.arn
    })
  }
  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy.lambda]

}
resource "aws_apigatewayv2_api" "http" {

  name          = "${local.prefix}-api"
  protocol_type = "HTTP"
  cors_configuration {

    allow_origins = ["https://${aws_cloudfront_distribution.web.domain_name}"]
    allow_methods = ["GET", "POST", "PUT", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]

  }

}
resource "aws_apigatewayv2_stage" "http" {

  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 25
  }

}
resource "aws_apigatewayv2_integration" "service" {

  for_each               = local.services
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api[each.key].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000

}
locals {

  routes = merge({
    for s in local.services : "ANY /api/${s}/{proxy+}" => s
    }, {

    "ANY /api/projects" = "projects", "GET /api/health" = "identity"

  })

}
resource "aws_apigatewayv2_route" "service" {

  for_each           = local.routes
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = each.key
  authorization_type = each.key == "GET /api/health" ? "NONE" : "JWT"
  authorizer_id      = each.key == "GET /api/health" ? null : aws_apigatewayv2_authorizer.identity.id
  target             = "integrations/${aws_apigatewayv2_integration.service[each.value].id}"

}
resource "aws_lambda_permission" "http" {

  for_each      = local.services
  statement_id  = "GatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"

}

resource "aws_apigatewayv2_route" "preflight" {
  # Match each protected route's path specificity so its ANY method cannot
  # capture OPTIONS and require a JWT before the browser can send one.
  for_each = { for route, service in local.routes : "OPTIONS ${split(" ", route)[1]}" => service }

  api_id             = aws_apigatewayv2_api.http.id
  route_key          = each.key
  authorization_type = "NONE"
  target             = "integrations/${aws_apigatewayv2_integration.service[each.value].id}"
}

resource "aws_apigatewayv2_authorizer" "identity" {
  api_id           = aws_apigatewayv2_api.http.id
  name             = "${local.prefix}-jwt"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    audience = [aws_cognito_user_pool_client.web.id]
    issuer   = local.env.JWT_ISSUER
  }
}

locals {
  task_templates = {
    for kind in ["worker", "runner"] : kind => {
      family                  = "${local.prefix}-${kind}"
      requiresCompatibilities = ["FARGATE"]
      networkMode             = "awsvpc"
      cpu                     = "1024"
      memory                  = "4096"
      executionRoleArn        = aws_iam_role.execution.arn
      taskRoleArn             = aws_iam_role.task[kind].arn
      runtimePlatform         = { cpuArchitecture = "X86_64", operatingSystemFamily = "LINUX" }
      volumes                 = [{ name = "tmp" }]
      containerDefinitions = [{

        name                   = "task", image = var.worker_image, essential = true,
        readonlyRootFilesystem = true, user = "10001", stopTimeout = 60,
        mountPoints = [{
          sourceVolume = "tmp", containerPath = "/tmp", readOnly = false
        }],
        environment = [for k, v in local.env : {
          name = k, value = v
        }],
        secrets = kind == "worker" && var.bigdata_secret_arn != "" ? [{
          name = "BIGDATA_API_KEY", valueFrom = var.bigdata_secret_arn
        }] : [],
        logConfiguration = {
          logDriver = "awslogs", options = {
            "awslogs-group" = aws_cloudwatch_log_group.worker.name, "awslogs-region" = var.region, "awslogs-stream-prefix" = kind, "mode" = "blocking"
          }
        }

      }]
    }
  }
  workflow_definition = {
    StartAt = local.stages[0], TimeoutSeconds = 10800, States = merge({

      for i, stage in local.stages : stage => merge({

        Type = "Task", Resource = "arn:aws:states:::ecs:runTask.sync", TimeoutSeconds = 1800,
        Parameters = {

          Cluster        = aws_ecs_cluster.compute.arn, LaunchType = "FARGATE", PlatformVersion = "LATEST",
          TaskDefinition = aws_ecs_task_definition.task[contains(["run", "extension_run"], stage) ? "runner" : "worker"].arn,
          NetworkConfiguration = {
            AwsvpcConfiguration = {
              Subnets = [aws_subnet.private.id], AssignPublicIp = "DISABLED", SecurityGroups = [contains(["run", "extension_run"], stage) ? aws_security_group.runner.id : aws_security_group.worker.id]
            }
          },
          Overrides = {
            ContainerOverrides = [{
              Name = "task", Environment = [{
                Name = "STAGE", Value = stage
                }, {
                Name = "PROJECT_ID", "Value.$" = "$.project_id"
                }, {
                Name = "RUN_ID", "Value.$" = "$.run_id"
              }]
            }]
          }

        },
        ResultPath = null,
        Retry = [{
          ErrorEquals = ["ECS.AmazonECSException", "ECS.ServerException"], IntervalSeconds = 5, MaxAttempts = 2, BackoffRate = 2
        }],
        Catch = [{
          ErrorEquals = ["States.ALL"], ResultPath = "$.failure", Next = "Failed"
        }]

        }, i == length(local.stages) - 1 ? {
        End = true
        } : {
        Next = local.stages[i + 1]
      })

      }, {
      Failed = {
        Type = "Fail", Error = "ExperimentWorkflowFailed", Cause = "Inspect persisted stage events and task logs"
      }
    })
  }
}
