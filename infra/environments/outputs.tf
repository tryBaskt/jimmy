output "frontend_url" {
  value = "https://${aws_cloudfront_distribution.web.domain_name}"
}
output "frontend_bucket" {
  value = aws_s3_bucket.web.id
}
output "distribution_id" {
  value = aws_cloudfront_distribution.web.id
}
output "api_url" {
  value = aws_apigatewayv2_api.http.api_endpoint
}
output "websocket_url" {
  value = "${aws_apigatewayv2_api.live.api_endpoint}/${var.environment}"
}
output "cognito_domain" {
  value = "https://${aws_cognito_user_pool_domain.web.domain}.auth.${var.region}.amazoncognito.com"
}
output "cognito_client_id" {
  value = aws_cognito_user_pool_client.web.id
}
output "user_pool_id" {
  value = aws_cognito_user_pool.users.id
}
output "workflow_arn" {
  value = aws_sfn_state_machine.research.arn
}
output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.id
}
