resource "aws_dynamodb_table" "metadata" {

  name         = "${local.prefix}-metadata"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"
  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
  stream_enabled   = true
  stream_view_type = "NEW_IMAGE"
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled = true
  }
  deletion_protection_enabled = var.environment == "prod"

}
resource "aws_s3_bucket" "artifacts" {
  bucket = "${local.prefix}-artifacts-${local.account}"
}
resource "aws_s3_bucket" "web" {
  # Keep this ground-up workspace separate from the legacy app's frontend bucket.
  bucket = "${local.prefix}-workspace-frontend-${local.account}"
}
resource "aws_s3_bucket_public_access_block" "private" {

  for_each = {
    artifacts = aws_s3_bucket.artifacts.id, web = aws_s3_bucket.web.id
  }
  bucket                  = each.value
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true

}
resource "aws_s3_bucket_server_side_encryption_configuration" "encrypted" {

  for_each = {
    artifacts = aws_s3_bucket.artifacts.id, web = aws_s3_bucket.web.id
  }
  bucket = each.value
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }

}
resource "aws_s3_bucket_versioning" "artifacts" {

  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }

}
resource "aws_s3_bucket_cors_configuration" "uploads" {

  bucket = aws_s3_bucket.artifacts.id
  cors_rule {

    allowed_headers = ["*"]
    allowed_methods = ["POST", "PUT", "GET"]
    allowed_origins = ["https://${aws_cloudfront_distribution.web.domain_name}"]
    expose_headers  = ["ETag"]
    max_age_seconds = 300

  }

}
resource "aws_s3_bucket_lifecycle_configuration" "staging" {

  bucket = aws_s3_bucket.artifacts.id
  rule {

    id     = "expire-staging-only"
    status = "Enabled"
    filter {
      prefix = "staging/"
    }
    expiration {
      days = 2
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }

  }

}
resource "aws_s3_bucket_policy" "artifacts_tls" {

  bucket = aws_s3_bucket.artifacts.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"], Condition = {
        Bool = {
          "aws:SecureTransport" = "false"
        }
      }
    }]
  })

}
