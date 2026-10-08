locals {
  database_secret_name     = var.database_secret_name != "" ? var.database_secret_name : "${var.project_name}/${var.environment}/database"
  staging_auth_secret_name = var.staging_auth_secret_name != "" ? var.staging_auth_secret_name : "${var.project_name}/${var.environment}/auth-token"
  kms_key_arn              = var.create_kms_key ? aws_kms_key.this[0].arn : null
}

resource "aws_kms_key" "this" {
  count = var.create_kms_key ? 1 : 0

  description             = "KMS key for Telemetry Lab staging resources"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-kms"
  })
}

resource "aws_kms_alias" "this" {
  count = var.create_kms_key ? 1 : 0

  name          = "alias/${var.name_prefix}"
  target_key_id = aws_kms_key.this[0].key_id
}

resource "random_password" "database" {
  length  = 32
  special = true
  # RDS master passwords cannot contain /, @, double quotes, or spaces.
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "random_password" "staging_auth" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "database" {
  name                           = local.database_secret_name
  kms_key_id                     = local.kms_key_arn
  force_overwrite_replica_secret = true
  recovery_window_in_days        = 0

  tags = merge(var.tags, {
    Name = local.database_secret_name
  })
}

resource "aws_secretsmanager_secret" "staging_auth" {
  name                           = local.staging_auth_secret_name
  kms_key_id                     = local.kms_key_arn
  force_overwrite_replica_secret = true
  recovery_window_in_days        = 0

  tags = merge(var.tags, {
    Name = local.staging_auth_secret_name
  })
}

resource "aws_secretsmanager_secret_version" "staging_auth" {
  secret_id     = aws_secretsmanager_secret.staging_auth.id
  secret_string = random_password.staging_auth.result
}
