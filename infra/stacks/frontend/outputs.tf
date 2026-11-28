output "bucket_name" {
  description = "S3 bucket that holds the built web app."
  value       = aws_s3_bucket.web.id
}

output "distribution_id" {
  description = "CloudFront distribution ID, used for cache invalidation."
  value       = aws_cloudfront_distribution.web.id
}

output "url" {
  description = "Public URL of the web app."
  value       = "https://${aws_cloudfront_distribution.web.domain_name}"
}
