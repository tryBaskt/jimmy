resource "aws_dynamodb_table" "user_sessions" {
  name         = "jimmy-dev-user-sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"

  attribute {
    name = "session_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery { enabled = true }
  server_side_encryption { enabled = true }
  tags = { Purpose = "user-sessions" }
}

resource "aws_dynamodb_table" "strategy_jobs" {
  name         = "jimmy-dev-strategy-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery { enabled = true }
  server_side_encryption { enabled = true }
  tags = { Purpose = "strategy-jobs" }
}

resource "aws_sqs_queue" "strategy_dead_letter" {
  name                      = "jimmy-dev-strategy-jobs-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "strategy_jobs" {
  name                       = "jimmy-dev-strategy-jobs"
  receive_wait_time_seconds  = 20
  visibility_timeout_seconds = 300
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.strategy_dead_letter.arn
    maxReceiveCount     = 1
  })
}

resource "aws_cloudwatch_metric_alarm" "strategy_dead_letter" {
  alarm_name          = "jimmy-dev-strategy-jobs-dlq"
  alarm_description   = "A strategy worker stopped before acknowledging a job."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    QueueName = aws_sqs_queue.strategy_dead_letter.name
  }
}

resource "aws_s3_bucket" "strategy_results" {
  bucket = "jimmy-dev-strategy-results-499133675835"
}

resource "aws_s3_bucket_public_access_block" "strategy_results" {
  bucket                  = aws_s3_bucket.strategy_results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "strategy_results" {
  bucket = aws_s3_bucket.strategy_results.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_policy" "strategy_results" {
  bucket = aws_s3_bucket.strategy_results.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.strategy_results.arn, "${aws_s3_bucket.strategy_results.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.strategy_results]
}

resource "aws_s3_bucket_lifecycle_configuration" "strategy_results" {
  bucket = aws_s3_bucket.strategy_results.id
  rule {
    id     = "expire-results"
    status = "Enabled"
    filter {}
    expiration { days = 30 }
  }
}

resource "aws_ecr_repository" "strategy_worker" {
  name                 = "jimmy-dev-strategy-worker"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}

resource "aws_ecr_lifecycle_policy" "strategy_worker" {
  repository = aws_ecr_repository.strategy_worker.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 20 latest worker images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}

output "user_sessions_table_name" { value = aws_dynamodb_table.user_sessions.name }
output "strategy_jobs_table_name" { value = aws_dynamodb_table.strategy_jobs.name }
output "strategy_queue_url" { value = aws_sqs_queue.strategy_jobs.url }
output "strategy_queue_arn" { value = aws_sqs_queue.strategy_jobs.arn }
output "strategy_results_bucket" { value = aws_s3_bucket.strategy_results.id }
output "strategy_worker_repository_url" { value = aws_ecr_repository.strategy_worker.repository_url }
