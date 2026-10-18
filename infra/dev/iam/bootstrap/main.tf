terraform {
  required_version = ">= 1.5.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = ["499133675835"]

  default_tags {
    tags = {
      Project     = "jimmy"
      Environment = "dev"
      ManagedBy   = "terraform"
    }
  }
}

locals {
  deployment_role_arn = "arn:aws:iam::499133675835:role/jimmy-dev-github-actions"
}

# This administrator-owned boundary caps the policies installed by the IAM job.
# The IAM job cannot change the boundary document or remove the boundary.
resource "aws_iam_policy" "deployment_boundary" {
  name        = "jimmy-dev-deployment-boundary"
  description = "Maximum permissions for the Jimmy dev DynamoDB deployment role"
  policy      = file("${path.module}/../dynamodb-provisioning.json")
}

resource "aws_iam_role" "iam_workflow" {
  name                 = "jimmy-dev-iam"
  description          = "Manage the bounded Jimmy dev deployment role policy"
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = "arn:aws:iam::499133675835:oidc-provider/token.actions.githubusercontent.com"
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:tryBaskt@287338800/jimmy@1423943322:environment:dev-iam"
        }
      }
    }]
  })

  tags = {
    Purpose = "iam"
  }
}

resource "aws_iam_role_policy" "manage_deployment_policy" {
  name = "jimmy-dev-manage-deployment-policy"
  role = aws_iam_role.iam_workflow.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadOnlyDevDeploymentRole"
        Effect   = "Allow"
        Action   = ["iam:GetRole", "iam:GetRolePolicy"]
        Resource = local.deployment_role_arn
      },
      {
        Sid      = "WritePolicyOnlyWithApprovedBoundary"
        Effect   = "Allow"
        Action   = "iam:PutRolePolicy"
        Resource = local.deployment_role_arn
        Condition = {
          ArnEquals = {
            "iam:PermissionsBoundary" = aws_iam_policy.deployment_boundary.arn
          }
        }
      },
      {
        Sid      = "InstallOnlyApprovedBoundary"
        Effect   = "Allow"
        Action   = "iam:PutRolePermissionsBoundary"
        Resource = local.deployment_role_arn
        Condition = {
          ArnEquals = {
            "iam:PermissionsBoundary" = aws_iam_policy.deployment_boundary.arn
          }
        }
      }
    ]
  })
}

output "iam_workflow_role_arn" {
  value = aws_iam_role.iam_workflow.arn
}

output "deployment_boundary_arn" {
  value = aws_iam_policy.deployment_boundary.arn
}
