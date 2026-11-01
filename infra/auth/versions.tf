terraform {
  required_version = ">= 1.11, < 2"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.71, < 7" }
  }
  backend "s3" {}
}
variable "foundation" { type = any }
provider "aws" {
  region = var.foundation.region
  default_tags { tags = { Application = "jimmy", Environment = var.foundation.environment, ManagedBy = "terraform" } }
}
locals {
  prefix  = "jimmy-${var.foundation.environment}"
  account = var.foundation.account
}
