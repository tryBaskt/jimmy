resource "aws_ecr_repository" "service" {
  for_each             = local.workflow_repositories
  name                 = "${local.prefix}-${replace(each.value.id, "_", "-")}-${each.value.kind}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

# Lambda pulls images as a service principal, separately from the execution role.
resource "aws_ecr_repository_policy" "api" {
  for_each   = { for key, repo in local.workflow_repositories : key => repo if repo.kind == "api" }
  repository = aws_ecr_repository.service[each.key].name
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Sid       = "LambdaImageRetrieval", Effect = "Allow",
    Principal = { Service = "lambda.amazonaws.com" },
    Action    = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
    Condition = { ArnLike = { "aws:SourceArn" = "arn:aws:lambda:${var.region}:${local.account}:function:${local.prefix}-${replace(each.value.id, "_", "-")}-api" } }
  }] })
}

resource "aws_s3_bucket" "web" {
  bucket = "${local.prefix}-web-${local.account}"
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${local.prefix}-web"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  comment             = "${local.prefix}-web"
  default_root_object = "index.html"
  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
    s3_origin_config { origin_access_identity = "" }
  }
  default_cache_behavior {
    target_origin_id       = "web"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    # Configuration and index updates must not retain old auth settings.
    cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
  }
  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate { cloudfront_default_certificate = true }
  price_class = "PriceClass_100"
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "cloudfront.amazonaws.com" },
    Action    = "s3:GetObject", Resource = "${aws_s3_bucket.web.arn}/*",
    Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.web.arn } }
  }] })
  depends_on = [aws_s3_bucket_public_access_block.web]
}

resource "aws_apigatewayv2_api" "api" {
  name          = "${local.prefix}-api"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins  = ["https://${aws_cloudfront_distribution.web.domain_name}"]
    allow_methods  = ["GET", "POST", "OPTIONS"]
    allow_headers  = ["authorization", "content-type"]
    expose_headers = ["content-disposition"]
  }
}

resource "aws_apigatewayv2_stage" "api" {
  provider    = aws.stage
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
  tags        = {}
  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }
}

output "deployment" {
  depends_on = [aws_secretsmanager_secret_version.openai]
  value = {
    openai_api_key_secret_arn = try(aws_secretsmanager_secret.openai[0].arn, "")
    logs_key_arn              = aws_kms_key.logs.arn
    environment               = var.environment
    account                   = local.account
    region                    = var.region
    repositories              = { for workflow in local.workflows : workflow.id => { api = aws_ecr_repository.service["${workflow.id}/api"].repository_url, runtime = aws_ecr_repository.service["${workflow.id}/runtime"].repository_url } }
    workflows                 = local.workflows
    frontend_bucket           = aws_s3_bucket.web.id
    distribution_id           = aws_cloudfront_distribution.web.id
    web_origin                = "https://${aws_cloudfront_distribution.web.domain_name}"
    api_id                    = aws_apigatewayv2_api.api.id
    api_url                   = aws_apigatewayv2_api.api.api_endpoint
  }
}
