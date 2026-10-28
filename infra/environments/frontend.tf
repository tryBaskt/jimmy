resource "aws_cloudfront_origin_access_control" "web" {

  name                              = "${local.prefix}-frontend-origin"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"

}
resource "aws_cloudfront_response_headers_policy" "security" {

  name = "${local.prefix}-frontend-security"
  security_headers_config {

    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }

  }

}
resource "aws_cloudfront_distribution" "web" {

  comment             = "${local.prefix}-frontend"
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  origin {

    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id

  }
  default_cache_behavior {

    target_origin_id           = "frontend"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = "658327ea-f89d-4fab-a63d-7e88639e58f6"
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
    compress                   = true

  }
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
  viewer_certificate {
    cloudfront_default_certificate = true
  }

}
resource "aws_s3_bucket_policy" "web" {

  bucket = aws_s3_bucket.web.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      {
        Effect = "Allow", Principal = {
          Service = "cloudfront.amazonaws.com"
          }, Action = "s3:GetObject", Resource = "${aws_s3_bucket.web.arn}/*", Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.web.arn
          }
        }
      },
      {
        Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.web.arn, "${aws_s3_bucket.web.arn}/*"], Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })

}
resource "aws_cognito_user_pool" "users" {

  name                     = "${local.prefix}-identity"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  admin_create_user_config {
    allow_admin_create_user_only = true
  }
  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

}
resource "aws_cognito_user_pool_client" "web" {

  name                                 = "${local.prefix}-web-client"
  user_pool_id                         = aws_cognito_user_pool.users.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = ["https://${aws_cloudfront_distribution.web.domain_name}/"]
  logout_urls                          = ["https://${aws_cloudfront_distribution.web.domain_name}/"]
  prevent_user_existence_errors        = "ENABLED"
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]

}
resource "aws_cognito_user_pool_domain" "web" {

  domain       = "${local.prefix}-auth-${local.account}"
  user_pool_id = aws_cognito_user_pool.users.id

}
