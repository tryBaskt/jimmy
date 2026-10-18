output "stock_metadata_table_name" {
  description = "DynamoDB table name for the stock metadata ingestion job."
  value       = aws_dynamodb_table.stock_metadata.name
}

output "stock_metadata_table_arn" {
  description = "DynamoDB table ARN for resource-scoped ingestion permissions."
  value       = aws_dynamodb_table.stock_metadata.arn
}

output "stock_universe_membership_table_name" {
  description = "DynamoDB table name for universe and symbol membership pairs."
  value       = aws_dynamodb_table.stock_universe_membership.name
}

output "stock_universe_membership_table_arn" {
  description = "DynamoDB membership table ARN for resource-scoped ingestion permissions."
  value       = aws_dynamodb_table.stock_universe_membership.arn
}
