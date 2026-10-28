variable "environment" {

  type = string
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod"
  }

}
variable "region" {
  type    = string
  default = "us-east-1"
}
variable "api_image" {
  type = string
}
variable "worker_image" {
  type = string
}
variable "planner_model_id" {
  type    = string
  default = "us.openai.gpt-6-astra"
}
variable "worker_model_id" {
  type    = string
  default = "us.moonshotai.kimi-k3"
}
variable "bedrock_model_arns" {

  description = "Regional foundation-model and inference-profile ARNs allowed for the configured models."
  type        = list(string)
  default     = ["arn:aws:bedrock:*::foundation-model/openai.gpt-6-astra*", "arn:aws:bedrock:*::foundation-model/moonshotai.kimi-k3*", "arn:aws:bedrock:*:*:inference-profile/us.openai.gpt-6-astra*", "arn:aws:bedrock:*:*:inference-profile/us.moonshotai.kimi-k3*"]

}
variable "bigdata_secret_arn" {
  type        = string
  default     = ""
  description = "Optional Secrets Manager ARN; ECS injects its value at runtime. Never put the API key in Terraform."
}
variable "yfinance_shared_cache" {
  type        = bool
  default     = false
  description = "Enable cross-user Yahoo caching only when provider terms permit this use."
}
variable "alert_email" {
  type    = string
  default = ""
}
