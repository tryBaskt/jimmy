variable "environment" {
  description = "Deployment environment name, e.g. dev2. Matches the GitHub environment and branch."
  type        = string
}

variable "region" {
  description = "AWS region for this stack."
  type        = string
  default     = "us-east-1"
}
