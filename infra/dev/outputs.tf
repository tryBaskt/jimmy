output "stock_metadata_table_name" {
  description = "DynamoDB table name for the stock metadata ingestion job."
  value       = aws_dynamodb_table.stock_metadata.name
}

output "stock_metadata_table_arn" {
  description = "DynamoDB table ARN for resource-scoped ingestion permissions."
  value       = aws_dynamodb_table.stock_metadata.arn
}
