resource "aws_secretsmanager_secret" "fmp_api_key" {
  name                    = "jimmy-dev-fmp-api-key"
  description             = "Financial Modeling Prep API key for Jimmy stock ingestion"
  recovery_window_in_days = 30

  tags = {
    Purpose = "fmp-api-key"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# The key was uploaded directly from .env using an administrator session.
# Import metadata only: never put secret values in Terraform or its state.
import {
  to = aws_secretsmanager_secret.fmp_api_key
  id = "arn:aws:secretsmanager:us-east-1:499133675835:secret:jimmy-dev-fmp-api-key-5vTTSm"
}
