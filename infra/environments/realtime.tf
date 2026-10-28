resource "aws_apigatewayv2_api" "live" {

  name                       = "${local.prefix}-activity"
  protocol_type              = "WEBSOCKET"
  route_selection_expression = "$request.body.action"

}
resource "aws_apigatewayv2_stage" "live" {

  api_id      = aws_apigatewayv2_api.live.id
  name        = var.environment
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 25
  }

}
resource "aws_lambda_function" "realtime" {

  for_each      = toset(["connections", "publisher", "reconciler"])
  function_name = "${local.prefix}-${each.key}"
  role          = aws_iam_role.lambda[each.key].arn
  package_type  = "Image"
  image_uri     = var.api_image
  timeout       = 30
  memory_size   = 512
  image_config {
    command = [each.key == "connections" ? "paperlab.realtime.connection_handler" : each.key == "publisher" ? "paperlab.realtime.publisher" : "paperlab.reconcile.handler"]
  }
  environment {
    variables = merge(local.env, {
      WEBSOCKET_MANAGEMENT_ENDPOINT = "https://${aws_apigatewayv2_api.live.id}.execute-api.${var.region}.amazonaws.com/${var.environment}"
    })
  }
  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy.lambda]

}
resource "aws_apigatewayv2_integration" "live" {

  api_id           = aws_apigatewayv2_api.live.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.realtime["connections"].invoke_arn

}
resource "aws_apigatewayv2_route" "live" {

  for_each  = toset(["$connect", "$disconnect", "subscribe"])
  api_id    = aws_apigatewayv2_api.live.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.live.id}"

}
resource "aws_lambda_permission" "live" {

  statement_id  = "WebSocketInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.realtime["connections"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.live.execution_arn}/*/*"

}
resource "aws_lambda_event_source_mapping" "activity" {

  event_source_arn               = aws_dynamodb_table.metadata.stream_arn
  function_name                  = aws_lambda_function.realtime["publisher"].arn
  starting_position              = "LATEST"
  batch_size                     = 25
  maximum_retry_attempts         = 3
  bisect_batch_on_function_error = true
  filter_criteria {
    filter {
      pattern = jsonencode({
        eventName = ["INSERT"], dynamodb = {
          NewImage = {
            sk = {
              S = [{
                prefix = "event#"
              }]
            }
          }
        }
      })
    }
  }

}
resource "aws_sns_topic" "alerts" {
  name = "${local.prefix}-alerts"
}
# Deployment configures the email subscription outside Terraform so the private
# address never enters plans or state. Only this non-sensitive topic is managed here.
resource "aws_cloudwatch_metric_alarm" "workflow_failed" {

  alarm_name  = "${local.prefix}-workflow-failures"
  namespace   = "AWS/States"
  metric_name = "ExecutionsFailed"
  dimensions = {
    StateMachineArn = aws_sfn_state_machine.research.arn
  }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

}

resource "aws_cloudwatch_event_rule" "terminal" {
  name          = "${local.prefix}-terminal-executions"
  event_pattern = jsonencode({ source = ["aws.states"], "detail-type" = ["Step Functions Execution Status Change"], detail = { stateMachineArn = [aws_sfn_state_machine.research.arn], status = ["FAILED", "TIMED_OUT", "ABORTED"] } })
}
resource "aws_cloudwatch_event_target" "terminal" {
  rule = aws_cloudwatch_event_rule.terminal.name
  arn  = aws_lambda_function.realtime["reconciler"].arn
}
resource "aws_lambda_permission" "terminal" {
  statement_id  = "TerminalWorkflowEvent"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.realtime["reconciler"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.terminal.arn
}
