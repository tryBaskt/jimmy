terraform {
  backend "s3" {
    bucket              = "jimmy-dev-terraform-state-499133675835"
    key                 = "dev/worker/terraform.tfstate"
    region              = "us-east-1"
    allowed_account_ids = ["499133675835"]
    encrypt             = true
    use_lockfile        = true
  }
}
