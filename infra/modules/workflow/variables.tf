terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.71, < 7" }
  }
}
variable "foundation" { type = any }
variable "auth" { type = any }
variable "workflow" { type = object({ id = string, name = string, api_prefix = string, requires_openai = bool }) }
variable "api_image" { type = string }
variable "runtime_image" { type = string }
variable "openai_model" { type = string }
variable "openai_api_key_secret_arn" { type = string }
locals {
  prefix         = "jimmy-${var.foundation.environment}-${replace(var.workflow.id, "_", "-")}"
  runtime_prefix = "jimmy_${var.foundation.environment}_${var.workflow.id}"
}
