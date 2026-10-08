output "tf_state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "tf_state_lock_table" {
  value = aws_dynamodb_table.lock.name
}

output "aws_lambda_artifact_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "kms_key_arn" {
  value = local.kms_key_arn
}

output "aws_oidc_role_arn" {
  value = local.create_oidc_role ? aws_iam_role.gitlab_oidc[0].arn : null
}
