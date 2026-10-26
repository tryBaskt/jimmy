output "stock_metadata_table_name" {
  description = "DynamoDB table name for the stock metadata ingestion job."
  value       = aws_dynamodb_table.stock_metadata.name
}

output "stock_metadata_table_arn" {
  description = "DynamoDB table ARN for resource-scoped ingestion permissions."
  value       = aws_dynamodb_table.stock_metadata.arn
}

output "stock_universe_membership_table_name" {
  description = "DynamoDB table name for dated universe membership intervals."
  value       = aws_dynamodb_table.stock_universe_membership.name
}

output "stock_universe_membership_table_arn" {
  description = "DynamoDB membership table ARN for resource-scoped ingestion permissions."
  value       = aws_dynamodb_table.stock_universe_membership.arn
}

output "fmp_api_key_secret_arn" {
  description = "FMP secret ARN; the secret value is never a Terraform output."
  value       = aws_secretsmanager_secret.fmp_api_key.arn
}

output "stock_gics_history_table_name" {
  description = "DynamoDB table name for dated GICS classifications."
  value       = aws_dynamodb_table.stock_gics_history.name
}

output "stock_gics_history_table_arn" {
  description = "DynamoDB GICS history table ARN for scoped ingestion permissions."
  value       = aws_dynamodb_table.stock_gics_history.arn
}
