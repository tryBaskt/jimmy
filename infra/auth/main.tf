resource "aws_cognito_user_pool" "users" {
  name                     = "${local.prefix}-users"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  deletion_protection      = var.foundation.environment == "prod" ? "ACTIVE" : "INACTIVE"
  admin_create_user_config { allow_admin_create_user_only = false }
  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }
}

resource "aws_cognito_user_pool_domain" "users" {
  domain       = "${local.prefix}-auth-${local.account}"
  user_pool_id = aws_cognito_user_pool.users.id
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "${local.prefix}-web"
  user_pool_id                         = aws_cognito_user_pool.users.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = ["${var.foundation.web_origin}/"]
  logout_urls                          = ["${var.foundation.web_origin}/"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  id_token_validity                    = 1
  token_validity_units { id_token = "hours" }
}

resource "aws_apigatewayv2_authorizer" "users" {
  api_id           = var.foundation.api_id
  name             = "${local.prefix}-authorizer"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    audience = [aws_cognito_user_pool_client.web.id]
    issuer   = "https://cognito-idp.${var.foundation.region}.amazonaws.com/${aws_cognito_user_pool.users.id}"
  }
}

output "deployment" {
  value = {
    environment       = var.foundation.environment
    authorizer_id     = aws_apigatewayv2_authorizer.users.id
    cognito_issuer    = "https://cognito-idp.${var.foundation.region}.amazonaws.com/${aws_cognito_user_pool.users.id}"
    cognito_client_id = aws_cognito_user_pool_client.web.id
    cognito_domain    = "https://${aws_cognito_user_pool_domain.users.domain}.auth.${var.foundation.region}.amazoncognito.com"
  }
}
