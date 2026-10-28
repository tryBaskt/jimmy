terraform {

  required_version = ">= 1.10, < 2"
  required_providers {
    aws = {
      source = "hashicorp/aws", version = "~> 6.0"
    }
  }

}
variable "region" {
  type    = string
  default = "us-east-1"
}
variable "repository" {
  type        = string
  default     = "tryBaskt/jimmy"
  description = "Repository portion of GitHub's OIDC subject. Use owner@owner_id/repo@repo_id when the repository emits ID-based subjects."
}
variable "github_oidc_provider_arn" {
  type        = string
  default     = ""
  description = "Supply the account's existing GitHub OIDC provider ARN, if one exists."
}
provider "aws" {
  region = var.region
}
data "aws_caller_identity" "current" {

}
locals {
  environments = toset(["dev", "prod"])
  account      = data.aws_caller_identity.current.account_id
}
resource "aws_iam_openid_connect_provider" "github" {

  count          = var.github_oidc_provider_arn == "" ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

}
resource "aws_ecr_repository" "images" {

  for_each             = toset(["dev-api", "dev-worker", "prod-api", "prod-worker"])
  name                 = "jimmy-${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }

}
resource "aws_s3_bucket" "state" {

  for_each = local.environments
  bucket   = "jimmy-${each.key}-state-${local.account}"

}
resource "aws_s3_bucket_versioning" "state" {

  for_each = local.environments
  bucket   = aws_s3_bucket.state[each.key].id
  versioning_configuration {
    status = "Enabled"
  }

}
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {

  for_each = local.environments
  bucket   = aws_s3_bucket.state[each.key].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }

}
resource "aws_s3_bucket_public_access_block" "state" {

  for_each                = local.environments
  bucket                  = aws_s3_bucket.state[each.key].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true

}
resource "aws_iam_role" "deploy" {

  for_each = local.environments
  name     = "jimmy-${each.key}-github-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Action = "sts:AssumeRoleWithWebIdentity", Principal = {
        Federated = var.github_oidc_provider_arn != "" ? var.github_oidc_provider_arn : aws_iam_openid_connect_provider.github[0].arn
        }, Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com", "token.actions.githubusercontent.com:sub" = "repo:${var.repository}:environment:${each.key}"
        }
      }
    }]
  })

}
# Bootstrap administrator runs this once. CI roles cannot modify each other or the OIDC trust.
resource "aws_iam_role_policy" "deploy" {

  for_each = local.environments
  name     = "jimmy-${each.key}-deployment-access"
  role     = aws_iam_role.deploy[each.key].id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      {
        Effect = "Allow", Action = ["s3:*"], Resource = ["arn:aws:s3:::jimmy-${each.key}-*", "arn:aws:s3:::jimmy-${each.key}-*/*"]
      },
      {
        Effect = "Allow", Action = ["dynamodb:*"], Resource = "arn:aws:dynamodb:${var.region}:${local.account}:table/jimmy-${each.key}-*"
      },
      {
        Effect = "Allow", Action = ["lambda:*"], Resource = "arn:aws:lambda:${var.region}:${local.account}:function:jimmy-${each.key}-*"
      },
      {
        Effect = "Allow", Action = ["states:*"], Resource = ["arn:aws:states:${var.region}:${local.account}:stateMachine:jimmy-${each.key}-*", "arn:aws:states:${var.region}:${local.account}:execution:jimmy-${each.key}-*:*"]
      },
      {
        Effect = "Allow", Action = ["ecr:*"], Resource = "arn:aws:ecr:${var.region}:${local.account}:repository/jimmy-${each.key}-*"
      },
      {
        Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*"
      },
      {
        Effect = "Allow", Action = ["secretsmanager:CreateSecret", "secretsmanager:PutSecretValue"], Resource = [
          "arn:aws:secretsmanager:${var.region}:${local.account}:secret:jimmy-${each.key}-bigdata-api-key-??????",
          "arn:aws:secretsmanager:${var.region}:${local.account}:secret:jimmy-${each.key}-alert-email-??????"
        ]
      },
      {
        Effect = "Allow", Action = ["logs:*"], Resource = ["arn:aws:logs:${var.region}:${local.account}:log-group:/aws/lambda/jimmy-${each.key}-*", "arn:aws:logs:${var.region}:${local.account}:log-group:/ecs/jimmy-${each.key}-*"]
      },
      {
        Effect = "Allow", Action = ["iam:CreateRole", "iam:GetRole", "iam:DeleteRole", "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole", "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:PassRole"], Resource = "arn:aws:iam::${local.account}:role/jimmy-${each.key}/jimmy-${each.key}-*"
      },
      {
        Effect = "Allow", Action = ["iam:CreateServiceLinkedRole"], Resource = "arn:aws:iam::${local.account}:role/aws-service-role/ecs.amazonaws.com/*", Condition = {
          StringEquals = {
            "iam:AWSServiceName" = "ecs.amazonaws.com"
          }
        }
      },
      {
        Effect = "Allow", Action = ["ec2:*", "ecs:*", "apigateway:*", "cloudfront:*", "cognito-idp:*", "cloudwatch:*", "sns:*", "events:*"], Resource = "*"
      }
    ]
  })

}
output "deployment_roles" {
  value = {
    for env, role in aws_iam_role.deploy : env => role.arn
  }
}
output "state_buckets" {
  value = {
    for env, bucket in aws_s3_bucket.state : env => bucket.id
  }
}
output "repositories" {
  value = {
    for name, repo in aws_ecr_repository.images : name => repo.repository_url
  }
}
