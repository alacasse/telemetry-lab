provider "aws" {
  region                      = var.aws_region
  skip_credentials_validation = var.skip_aws_credentials_validation
  skip_metadata_api_check     = var.skip_aws_credentials_validation
  skip_region_validation      = var.skip_aws_credentials_validation
  skip_requesting_account_id  = var.skip_aws_credentials_validation
}

locals {
  name_prefix           = "${var.project_name}-${var.environment}"
  budget_alerts_enabled = length(var.budget_notification_emails) > 0 || length(var.budget_notification_sns_topic_arns) > 0
  common_tags = merge(
    {
      project     = var.project_name
      environment = var.environment
      managed_by  = "terraform"
      ttl         = "ephemeral"
    },
    var.tags,
  )
}

module "networking" {
  source = "./modules/networking"

  name_prefix        = local.name_prefix
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones
  aws_region         = var.aws_region
  tags               = local.common_tags
}

module "security" {
  source = "./modules/security"

  project_name             = var.project_name
  environment              = var.environment
  name_prefix              = local.name_prefix
  create_kms_key           = var.create_kms_key
  database_secret_name     = var.database_secret_name
  staging_auth_secret_name = var.staging_auth_secret_name
  tags                     = local.common_tags
}

module "database" {
  source = "./modules/database"

  name_prefix             = local.name_prefix
  db_name                 = var.db_name
  db_username             = var.db_username
  db_password             = module.security.database_password
  subnet_ids              = module.networking.data_private_subnet_ids
  security_group_ids      = [module.networking.database_security_group_id]
  instance_class          = var.db_instance_class
  allocated_storage       = var.db_allocated_storage
  backup_retention_period = var.db_backup_retention_period
  skip_final_snapshot     = var.skip_final_snapshot
  kms_key_id              = module.security.kms_key_arn
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "database" {
  secret_id = module.security.database_secret_id
  secret_string = jsonencode({
    engine   = "postgresql+psycopg"
    host     = module.database.address
    port     = module.database.port
    dbname   = module.database.database_name
    username = module.database.db_username
    password = module.security.database_password
  })

  depends_on = [module.database]
}

module "queue" {
  source = "./modules/queue"

  name_prefix                   = local.name_prefix
  queue_name                    = var.queue_name
  visibility_timeout_seconds    = var.queue_visibility_timeout_seconds
  message_retention_seconds     = var.queue_message_retention_seconds
  dead_letter_retention_seconds = var.dlq_message_retention_seconds
  max_receive_count             = var.queue_max_receive_count
  kms_key_id                    = module.security.kms_key_arn
  tags                          = local.common_tags
}

module "iam" {
  source = "./modules/iam"

  name_prefix             = local.name_prefix
  queue_arn               = module.queue.queue_arn
  database_secret_arn     = module.security.database_secret_arn
  staging_auth_secret_arn = module.security.staging_auth_secret_arn
  kms_key_arn             = module.security.kms_key_arn
  tags                    = local.common_tags
}

module "lambda_services" {
  source = "./modules/lambda-services"

  name_prefix     = local.name_prefix
  artifact_bucket = var.lambda_artifact_bucket
  artifact_keys = {
    ingestion = "lambdas/${var.lambda_artifact_revision}/ingestion.zip"
    query     = "lambdas/${var.lambda_artifact_revision}/query.zip"
    worker    = "lambdas/${var.lambda_artifact_revision}/worker.zip"
    migration = "lambdas/${var.lambda_artifact_revision}/migration.zip"
  }
  ingestion_role_arn      = module.iam.ingestion_role_arn
  query_role_arn          = module.iam.query_role_arn
  worker_role_arn         = module.iam.worker_role_arn
  migration_role_arn      = module.iam.migration_role_arn
  queue_name              = module.queue.queue_name
  queue_url               = module.queue.queue_url
  queue_arn               = module.queue.queue_arn
  database_secret_arn     = module.security.database_secret_arn
  staging_auth_secret_arn = module.security.staging_auth_secret_arn
  private_subnet_ids      = module.networking.app_private_subnet_ids
  security_group_ids      = [module.networking.lambda_security_group_id]
  aws_region              = var.aws_region
  log_level               = var.log_level
  enable_xray             = var.enable_xray
  release_revision        = var.lambda_artifact_revision
  tags                    = local.common_tags
}

module "api_gateway" {
  source = "./modules/api-gateway"

  name_prefix                 = local.name_prefix
  ingestion_lambda_name       = module.lambda_services.ingestion_function_name
  ingestion_lambda_invoke_arn = module.lambda_services.function_invoke_arns["ingestion"]
  query_lambda_name           = module.lambda_services.query_function_name
  query_lambda_invoke_arn     = module.lambda_services.function_invoke_arns["query"]
  tags                        = local.common_tags
}

module "observability" {
  source = "./modules/observability"

  name_prefix            = local.name_prefix
  aws_region             = var.aws_region
  function_names         = module.lambda_services.function_names
  api_id                 = module.api_gateway.api_id
  queue_name             = module.queue.queue_name
  dead_letter_queue_name = module.queue.dead_letter_queue_name
  database_identifier    = module.database.instance_identifier
  tags                   = local.common_tags
}

resource "aws_budgets_budget" "actual_spend" {
  count = local.budget_alerts_enabled ? 1 : 0

  name         = "${local.name_prefix}-actual-spend"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_actual_threshold_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = var.budget_notification_emails
    subscriber_sns_topic_arns  = var.budget_notification_sns_topic_arns
  }
}

resource "aws_budgets_budget" "forecast_spend" {
  count = local.budget_alerts_enabled ? 1 : 0

  name         = "${local.name_prefix}-forecast-spend"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_forecast_threshold_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "FORECASTED"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = var.budget_notification_emails
    subscriber_sns_topic_arns  = var.budget_notification_sns_topic_arns
  }
}
