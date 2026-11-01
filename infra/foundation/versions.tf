terraform {
  required_version = ">= 1.11, < 2"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.71, < 7" }
  }
  backend "s3" {}
}
provider "aws" {
  region = var.region
  default_tags { tags = { Application = "jimmy", Environment = var.environment, ManagedBy = "terraform" } }
}
variable "region" { type = string }
variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "Use dev or prod."
  }
}
data "aws_caller_identity" "current" {}
locals {
  workflows             = jsondecode(file("${path.module}/../../workflows.json")).workflows
  workflow_repositories = { for item in flatten([for workflow in local.workflows : [for kind in ["api", "runtime"] : { id = workflow.id, kind = kind }]]) : "${item.id}/${item.kind}" => item }
  prefix                = "jimmy-${var.environment}"
  account               = data.aws_caller_identity.current.account_id
}
