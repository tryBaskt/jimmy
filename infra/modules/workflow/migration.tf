# Stop managing the former runtime without deleting it during the ECS cutover.
# Administrators can retire the old runtime, roles and logs after verification.
removed {
  from = aws_bedrockagentcore_agent_runtime.runtime
  lifecycle { destroy = false }
}
removed {
  from = aws_cloudwatch_log_group.runtime
  lifecycle { destroy = false }
}
removed {
  from = aws_iam_role.runtime
  lifecycle { destroy = false }
}
removed {
  from = aws_iam_role_policy.runtime
  lifecycle { destroy = false }
}
removed {
  from = aws_iam_role_policy.openai_secret
  lifecycle { destroy = false }
}
