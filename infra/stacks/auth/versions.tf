terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Bucket, key and region are supplied at init time by the deploy-stack action:
  # bucket = jimmy-<environment>-state-<account>, key = auth/terraform.tfstate
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Application = "jimmy"
      Environment = var.environment
      Component   = "auth"
      ManagedBy   = "terraform"
    }
  }
}
