variable "openai_api_key" {
  type      = string
  sensitive = true
  ephemeral = true
  default   = ""
}

variable "openai_api_key_version" {
  type    = number
  default = 1
  validation {
    condition     = var.openai_api_key_version >= 1 && floor(var.openai_api_key_version) == var.openai_api_key_version
    error_message = "Secret version must be a positive integer."
  }
}

locals {
  requires_openai = anytrue([for workflow in local.workflows : workflow.requires_openai])
}

resource "aws_secretsmanager_secret" "openai" {
  count                   = local.requires_openai ? 1 : 0
  name                    = "${local.prefix}-openai-api-key"
  description             = "OpenAI API key for ${local.prefix} workflows"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "openai" {
  count                    = local.requires_openai ? 1 : 0
  secret_id                = aws_secretsmanager_secret.openai[0].id
  secret_string_wo         = var.openai_api_key
  secret_string_wo_version = var.openai_api_key_version

  lifecycle {
    precondition {
      condition     = trimspace(var.openai_api_key) != ""
      error_message = "Set the OPENAI_API_KEY GitHub environment secret before deploying."
    }
  }
}
