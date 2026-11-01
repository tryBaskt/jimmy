resource "aws_kms_key" "logs" {
  description             = "${local.prefix} application logs"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountKeyAdministration", Effect = "Allow",
        Principal = { AWS = "arn:aws:iam::${local.account}:root" },
        Action    = "kms:*", Resource = "*"
      },
      {
        Sid       = "CloudWatchLogsEncryption", Effect = "Allow",
        Principal = { Service = "logs.${var.region}.amazonaws.com" },
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"],
        Resource  = "*",
        Condition = { ArnLike = {
          "kms:EncryptionContext:aws:logs:arn" = flatten([for workflow in local.workflows : [
            "arn:aws:logs:${var.region}:${local.account}:log-group:/aws/bedrock-agentcore/runtimes/jimmy_${var.environment}_${workflow.id}*",
            "arn:aws:logs:${var.region}:${local.account}:log-group:/aws/lambda/${local.prefix}-${replace(workflow.id, "_", "-")}-api"
          ]])
        } }
      }
    ]
  })
}

resource "aws_kms_alias" "logs" {
  name          = "alias/${local.prefix}-logs"
  target_key_id = aws_kms_key.logs.key_id
}
