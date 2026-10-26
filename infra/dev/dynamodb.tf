resource "aws_dynamodb_table" "stock_metadata" {
  name         = "jimmy-dev-stock-metadata"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "security_id"

  attribute {
    name = "security_id"
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
  range_key    = "membership_key"

  attribute {
    name = "universe"
    type = "S"
  }

  attribute {
    name = "membership_key"
    type = "S"
  }

  tags = {
    Purpose = "stock-universe-membership"
  }
}

resource "aws_dynamodb_table" "stock_gics_history" {
  name         = "jimmy-dev-stock-gics-history"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "security_id"
  range_key    = "classification_key"

  attribute {
    name = "security_id"
    type = "S"
  }

  attribute {
    name = "classification_key"
    type = "S"
  }

  tags = {
    Purpose = "stock-gics-history"
  }
}
