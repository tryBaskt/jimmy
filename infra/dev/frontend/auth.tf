resource "aws_cognito_user_pool" "users" {
  name                     = "jimmy-dev-users"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  deletion_protection      = "ACTIVE"
  user_pool_tier           = "ESSENTIALS"

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_domain" "users" {
  domain                = "jimmy-dev-499133675835"
  user_pool_id          = aws_cognito_user_pool.users.id
  managed_login_version = 2
}

resource "aws_cognito_managed_login_branding" "web" {
  user_pool_id                = aws_cognito_user_pool.users.id
  client_id                   = aws_cognito_user_pool_client.web.id
  use_cognito_provided_values = true
  depends_on                  = [aws_cognito_user_pool_domain.users]
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "jimmy-dev-web"
  user_pool_id                         = aws_cognito_user_pool.users.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH"]
  callback_urls                        = ["https://${aws_cloudfront_distribution.frontend.domain_name}/api/auth/callback"]
  logout_urls                          = ["https://${aws_cloudfront_distribution.frontend.domain_name}/"]
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 30
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
  enable_token_revocation       = true
  prevent_user_existence_errors = "ENABLED"
  refresh_token_rotation {
    feature                    = "ENABLED"
    retry_grace_period_seconds = 10
  }
}

output "cognito_user_pool_id" { value = aws_cognito_user_pool.users.id }
output "cognito_client_id" { value = aws_cognito_user_pool_client.web.id }
output "cognito_domain" { value = "https://${aws_cognito_user_pool_domain.users.domain}.auth.us-east-1.amazoncognito.com" }
