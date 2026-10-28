terraform {

  required_version = ">= 1.10, < 2"
  required_providers {

    aws = {
      source = "hashicorp/aws", version = "~> 6.0"
    }

  }
  backend "s3" {

  }

}
provider "aws" {

  region = var.region
  default_tags {
    tags = {
      Application = "jimmy", Environment = var.environment, ManagedBy = "terraform"
    }
  }

}
data "aws_caller_identity" "current" {

}
data "aws_partition" "current" {

}
locals {

  prefix  = "jimmy-${var.environment}"
  account = data.aws_caller_identity.current.account_id
  stages  = ["ingest", "plan", "data", "freeze", "build", "run", "compare", "extend", "extension_data", "extension_freeze", "extension_build", "extension_run", "extension_evaluate", "report"]
  env = {

    PAPERLAB_MODE         = "aws", AWS_REGION = var.region,
    TABLE_NAME            = aws_dynamodb_table.metadata.name, ARTIFACT_BUCKET = aws_s3_bucket.artifacts.id,
    JWT_ISSUER            = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.users.id}",
    JWT_AUDIENCE          = aws_cognito_user_pool_client.web.id,
    PLANNER_MODEL_ID      = var.planner_model_id, WORKER_MODEL_ID = var.worker_model_id,
    WEB_ORIGIN            = "https://${aws_cloudfront_distribution.web.domain_name}",
    YFINANCE_SHARED_CACHE = tostring(var.yfinance_shared_cache)

  }

}
