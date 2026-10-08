provider "aws" {
  region = var.aws_region
}

locals {
  common_tags = merge(
    {
      project     = var.project_name
      environment = var.environment
      managed_by  = "terraform"
      scope       = "bootstrap"
    },
    var.tags,
  )
  kms_key_arn      = var.create_kms_key ? aws_kms_key.this[0].arn : null
  create_oidc_role = var.gitlab_oidc_provider_arn != "" && var.gitlab_project_path != ""
}

resource "aws_kms_key" "this" {
  count = var.create_kms_key ? 1 : 0

  description             = "KMS key for Telemetry Lab staging bootstrap resources"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-bootstrap-kms"
  })
}

resource "aws_kms_alias" "this" {
  count = var.create_kms_key ? 1 : 0

  name          = "alias/${var.project_name}-${var.environment}"
  target_key_id = aws_kms_key.this[0].key_id
}

resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = local.kms_key_arn
      sse_algorithm     = local.kms_key_arn == null ? "AES256" : "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket" "artifacts" {
  bucket = var.artifact_bucket_name
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = local.kms_key_arn
      sse_algorithm     = local.kms_key_arn == null ? "AES256" : "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "lock" {
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.common_tags
}

data "aws_iam_policy_document" "gitlab_assume_role" {
  count = local.create_oidc_role ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.gitlab_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "gitlab.com:aud"
      values   = ["https://gitlab.com"]
    }

    condition {
      test     = "StringLike"
      variable = "gitlab.com:sub"
      values   = ["project_path:${var.gitlab_project_path}:ref_type:branch:ref:${var.gitlab_default_branch}"]
    }
  }
}

resource "aws_iam_role" "gitlab_oidc" {
  count = local.create_oidc_role ? 1 : 0

  name               = "${var.project_name}-${var.environment}-gitlab-oidc"
  assume_role_policy = data.aws_iam_policy_document.gitlab_assume_role[0].json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "gitlab_deploy" {
  count = local.create_oidc_role ? 1 : 0

  statement {
    actions = [
      "apigateway:*",
      "budgets:*",
      "cloudwatch:*",
      "dynamodb:*",
      "ec2:*",
      "iam:*",
      "kms:*",
      "lambda:*",
      "logs:*",
      "rds:*",
      "s3:*",
      "secretsmanager:*",
      "sqs:*",
      "xray:*",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "gitlab_deploy" {
  count = local.create_oidc_role ? 1 : 0

  name   = "${var.project_name}-${var.environment}-gitlab-deploy"
  role   = aws_iam_role.gitlab_oidc[0].id
  policy = data.aws_iam_policy_document.gitlab_deploy[0].json
}
