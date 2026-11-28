output "user_pool_id" {
  description = "Cognito user pool ID for the web app."
  value       = aws_cognito_user_pool.users.id
}

output "user_pool_client_id" {
  description = "Public (secretless) Cognito app client ID for the web app."
  value       = aws_cognito_user_pool_client.web.id
}

output "user_pool_arn" {
  description = "Cognito user pool ARN, for future backend authorizers."
  value       = aws_cognito_user_pool.users.arn
}
