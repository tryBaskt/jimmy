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

resource "aws_dynamodb_table" "stock_universe_membership" {
  name         = "jimmy-dev-stock-universe-membership"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "universe"
  range_key    = "symbol"

  attribute {
    name = "universe"
    type = "S"
  }

  attribute {
    name = "symbol"
    type = "S"
  }

  tags = {
    Purpose = "stock-universe-membership"
  }
}
