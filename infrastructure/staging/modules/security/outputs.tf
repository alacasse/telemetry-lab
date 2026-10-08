output "database_password" {
  value     = random_password.database.result
  sensitive = true
}

output "database_secret_id" {
  value = aws_secretsmanager_secret.database.id
}

output "database_secret_arn" {
  value = aws_secretsmanager_secret.database.arn
}

output "staging_auth_secret_arn" {
  value = aws_secretsmanager_secret.staging_auth.arn
}

output "staging_auth_secret_name" {
  value = aws_secretsmanager_secret.staging_auth.name
}

output "kms_key_arn" {
  value = local.kms_key_arn
}
