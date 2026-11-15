resource "aws_dynamodb_table" "history" {
  name                        = "${local.prefix}-history"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "owner"
  range_key                   = "created_run"
  deletion_protection_enabled = true
  attribute {
    name = "owner"
    type = "S"
  }
  attribute {
    name = "created_run"
    type = "S"
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled = true
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "runs" {
  bucket = aws_s3_bucket.runs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_policy" "runs" {
  bucket = aws_s3_bucket.runs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "RequireTLS"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.runs.arn, "${aws_s3_bucket.runs.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}
