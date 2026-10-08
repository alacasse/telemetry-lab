locals {
  lambda_roles = {
    ingestion = aws_iam_role.ingestion.name
    query     = aws_iam_role.query.name
    worker    = aws_iam_role.worker.name
    migration = aws_iam_role.migration.name
  }
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingestion" {
  name               = "${var.name_prefix}-ingestion-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = var.tags
}

resource "aws_iam_role" "query" {
  name               = "${var.name_prefix}-query-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = var.tags
}

resource "aws_iam_role" "worker" {
  name               = "${var.name_prefix}-worker-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = var.tags
}

resource "aws_iam_role" "migration" {
  name               = "${var.name_prefix}-migration-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "basic_execution" {
  for_each = local.lambda_roles

  role       = each.value
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "vpc_execution" {
  for_each = {
    query     = aws_iam_role.query.name
    worker    = aws_iam_role.worker.name
    migration = aws_iam_role.migration.name
  }

  role       = each.value
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy_attachment" "xray_execution" {
  for_each = local.lambda_roles

  role       = each.value
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

data "aws_iam_policy_document" "ingestion_runtime_access" {
  statement {
    actions = [
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:SendMessage",
    ]
    resources = [var.queue_arn]
  }

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.staging_auth_secret_arn]
  }

  dynamic "statement" {
    for_each = var.kms_key_arn == null ? [] : [var.kms_key_arn]
    content {
      actions   = ["kms:Decrypt"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "ingestion_runtime_access" {
  name   = "${var.name_prefix}-ingestion-runtime-access"
  role   = aws_iam_role.ingestion.id
  policy = data.aws_iam_policy_document.ingestion_runtime_access.json
}

data "aws_iam_policy_document" "query_runtime_access" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.database_secret_arn, var.staging_auth_secret_arn]
  }

  dynamic "statement" {
    for_each = var.kms_key_arn == null ? [] : [var.kms_key_arn]
    content {
      actions   = ["kms:Decrypt"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "query_runtime_access" {
  name   = "${var.name_prefix}-query-runtime-access"
  role   = aws_iam_role.query.id
  policy = data.aws_iam_policy_document.query_runtime_access.json
}

data "aws_iam_policy_document" "database_runtime_access" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.database_secret_arn]
  }

  dynamic "statement" {
    for_each = var.kms_key_arn == null ? [] : [var.kms_key_arn]
    content {
      actions   = ["kms:Decrypt"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "worker_runtime_access" {
  name   = "${var.name_prefix}-worker-runtime-access"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.database_runtime_access.json
}

resource "aws_iam_role_policy" "migration_runtime_access" {
  name   = "${var.name_prefix}-migration-runtime-access"
  role   = aws_iam_role.migration.id
  policy = data.aws_iam_policy_document.database_runtime_access.json
}
