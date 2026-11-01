terraform {
  required_version = ">= 1.11, < 2"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.71, < 7" }
  }
  backend "s3" {}
}
variable "foundation" { type = any }
variable "auth" { type = any }
variable "workflow" {
  type = object({ id = string, name = string, api_prefix = string, requires_openai = bool })
  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{0,31}$", var.workflow.id)) && var.workflow.api_prefix == "/api/${replace(var.workflow.id, "_", "-")}"
    error_message = "Workflow ID and API prefix must follow the registry convention."
  }
}
variable "api_image" { type = string }
variable "runtime_image" { type = string }
variable "openai_model" {
  type    = string
  default = "gpt-6.1-sol"
}
provider "aws" {
  region = var.foundation.region
  default_tags { tags = { Application = "jimmy", Environment = var.foundation.environment, Workflow = var.workflow.id, ManagedBy = "terraform" } }
}
module "workflow" {
  source                    = "../modules/workflow"
  foundation                = var.foundation
  auth                      = var.auth
  workflow                  = var.workflow
  api_image                 = var.api_image
  runtime_image             = var.runtime_image
  openai_model              = var.openai_model
  openai_api_key_secret_arn = var.foundation.openai_api_key_secret_arn
}
output "deployment" { value = module.workflow.deployment }
