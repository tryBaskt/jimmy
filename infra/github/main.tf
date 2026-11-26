terraform {
  required_version = ">= 1.11, < 2"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.71, < 7" }
  }
}

variable "environment" {
  type    = string
  default = "dev"
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "Use dev or prod."
  }
}
variable "region" {
  type    = string
  default = "us-east-1"
}
provider "aws" {
  region = var.region
  default_tags { tags = { Application = "jimmy", Environment = var.environment, ManagedBy = "terraform" } }
}
data "aws_caller_identity" "current" {}
locals {
  prefix = "jimmy-${var.environment}"
  template_variables = {
    environment = var.environment
    region      = var.region
    account_id  = data.aws_caller_identity.current.account_id
  }
}
resource "aws_iam_role" "github_deploy" {
  name                 = "${local.prefix}-github-deploy"
  description          = "GitHub Actions deployment for tryBaskt/jimmy ${var.environment}"
  max_session_duration = 3600
  assume_role_policy   = templatefile("${path.module}/trust.json.tftpl", local.template_variables)
}
resource "aws_iam_role_policy" "github_deploy" {
  name   = "${local.prefix}-github-deploy-permissions"
  role   = aws_iam_role.github_deploy.id
  policy = templatefile("${path.module}/permissions.json.tftpl", local.template_variables)
}
output "role_arn" { value = aws_iam_role.github_deploy.arn }

resource "aws_iam_policy" "workflow_boundary" {
  name        = "${local.prefix}-workflow-permissions-boundary"
  description = "Maximum runtime and API permissions for ${local.prefix} workflows"
  policy      = templatefile("${path.module}/boundary.json.tftpl", local.template_variables)
}

resource "aws_iam_policy" "ecs_deploy" {
  name   = "${local.prefix}-ecs-deploy-permissions"
  policy = templatefile("${path.module}/ecs-permissions.json.tftpl", local.template_variables)
}

resource "aws_iam_role_policy_attachment" "ecs_deploy" {
  role       = aws_iam_role.github_deploy.name
  policy_arn = aws_iam_policy.ecs_deploy.arn
}
