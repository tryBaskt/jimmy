resource "aws_dynamodb_table" "stock_metadata" {
  name         = "jimmy-dev-stock-metadata"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "symbol"

  attribute {
    name = "symbol"
    type = "S"
  }

  tags = {
    Purpose = "stock-metadata"
  }
}
