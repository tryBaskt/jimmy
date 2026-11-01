mock_provider "aws" {}
mock_provider "aws" { alias = "stage" }

override_data {
  target = data.aws_caller_identity.current
  values = {
    account_id = "123456789012"
    arn        = "arn:aws:iam::123456789012:root"
    user_id    = "123456789012"
  }
}

override_resource {
  target = aws_secretsmanager_secret.openai[0]
  values = {
    id  = "arn:aws:secretsmanager:us-east-1:123456789012:secret:jimmy-dev-openai-api-key-test01"
    arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:jimmy-dev-openai-api-key-test01"
  }
}

variables {
  environment            = "dev"
  region                 = "us-east-1"
  openai_api_key         = "offline-test-key"
  openai_api_key_version = 42
}

run "provisions_named_secret_and_exports_arn" {
  command = apply

  assert {
    condition     = aws_secretsmanager_secret.openai[0].name == "jimmy-dev-openai-api-key"
    error_message = "OpenAI secret must follow the environment naming convention."
  }
  assert {
    condition     = aws_secretsmanager_secret_version.openai[0].secret_id == aws_secretsmanager_secret.openai[0].id
    error_message = "Secret value must be stored in the provisioned secret."
  }
  assert {
    condition     = aws_secretsmanager_secret_version.openai[0].secret_string_wo_version == 42
    error_message = "Deployment version must update the write-only secret value."
  }
  assert {
    condition     = output.deployment.openai_api_key_secret_arn == aws_secretsmanager_secret.openai[0].arn
    error_message = "The workflow must receive Terraform's secret ARN automatically."
  }
}

run "missing_github_key_blocks_deployment" {
  command = plan
  variables { openai_api_key = "" }
  expect_failures = [aws_secretsmanager_secret_version.openai[0]]
}

run "lambda_image_access_is_scoped_to_each_workflow_function" {
  command = apply
  assert {
    condition     = length(aws_ecr_repository_policy.api) == length(local.workflows)
    error_message = "Every workflow API requires a Lambda image retrieval policy."
  }
  assert {
    condition = alltrue([for key, repo in aws_ecr_repository_policy.api :
      jsondecode(repo.policy).Statement[0].Principal.Service == "lambda.amazonaws.com" &&
      toset(jsondecode(repo.policy).Statement[0].Action) == toset(["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]) &&
      jsondecode(repo.policy).Statement[0].Condition.ArnLike["aws:SourceArn"] == "arn:aws:lambda:us-east-1:123456789012:function:jimmy-dev-${replace(local.workflow_repositories[key].id, "_", "-")}-api"
    ])
    error_message = "Only the corresponding Lambda function may retrieve a workflow API image."
  }
}
