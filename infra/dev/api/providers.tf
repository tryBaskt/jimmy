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
