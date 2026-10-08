locals {
  base_environment = {
    APP_ENV          = "staging"
    ENABLE_XRAY      = tostring(var.enable_xray)
    LOG_LEVEL        = var.log_level
    QUEUE_BACKEND    = "sqs"
    QUEUE_NAME       = var.queue_name
    QUEUE_URL        = var.queue_url
    RELEASE_REVISION = var.release_revision
  }

  function_configs = {
    ingestion = {
      handler = "ingestion_service.lambda_handler.handler"
      environment_variables = merge(local.base_environment, {
        STAGING_AUTH_SECRET_ARN = var.staging_auth_secret_arn
      })
      role_arn             = var.ingestion_role_arn
      memory_size          = 256
      timeout              = 15
      reserved_concurrency = 2
      vpc_enabled          = false
    }
    query = {
      handler = "query_service.lambda_handler.handler"
      environment_variables = merge(local.base_environment, {
        DATABASE_SECRET_ARN     = var.database_secret_arn
        STAGING_AUTH_SECRET_ARN = var.staging_auth_secret_arn
      })
      role_arn             = var.query_role_arn
      memory_size          = 256
      timeout              = 15
      reserved_concurrency = 1
      vpc_enabled          = true
    }
    worker = {
      handler = "processing_worker.lambda_handler.handler"
      environment_variables = merge(local.base_environment, {
        DATABASE_SECRET_ARN = var.database_secret_arn
      })
      role_arn             = var.worker_role_arn
      memory_size          = 512
      timeout              = 60
      reserved_concurrency = 1
      vpc_enabled          = true
    }
    migration = {
      handler = "packages.db.migration_handler.handler"
      environment_variables = merge(local.base_environment, {
        DATABASE_SECRET_ARN = var.database_secret_arn
      })
      role_arn             = var.migration_role_arn
      memory_size          = 512
      timeout              = 120
      reserved_concurrency = 1
      vpc_enabled          = true
    }
  }
}

resource "aws_cloudwatch_log_group" "function" {
  for_each = local.function_configs

  name              = "/aws/lambda/${var.name_prefix}-${each.key}"
  retention_in_days = 14
  tags              = var.tags
}

resource "aws_lambda_function" "this" {
  for_each = local.function_configs

  function_name                  = "${var.name_prefix}-${each.key}"
  s3_bucket                      = var.artifact_bucket
  s3_key                         = var.artifact_keys[each.key]
  handler                        = each.value.handler
  runtime                        = "python3.12"
  architectures                  = ["x86_64"]
  role                           = each.value.role_arn
  memory_size                    = each.value.memory_size
  timeout                        = each.value.timeout
  reserved_concurrent_executions = each.value.reserved_concurrency
  package_type                   = "Zip"

  tracing_config {
    mode = var.enable_xray ? "Active" : "PassThrough"
  }

  environment {
    variables = each.value.environment_variables
  }

  dynamic "vpc_config" {
    for_each = each.value.vpc_enabled ? [1] : []
    content {
      subnet_ids         = var.private_subnet_ids
      security_group_ids = var.security_group_ids
    }
  }

  depends_on = [aws_cloudwatch_log_group.function]
  tags       = var.tags
}

resource "aws_lambda_event_source_mapping" "worker_queue" {
  event_source_arn        = var.queue_arn
  function_name           = aws_lambda_function.this["worker"].arn
  batch_size              = 5
  enabled                 = true
  function_response_types = ["ReportBatchItemFailures"]
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = aws_lambda_function.this

  alarm_name          = "${each.value.function_name}-errors"
  alarm_description   = "Telemetry Lab staging Lambda reports function errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  dimensions = {
    FunctionName = each.value.function_name
  }
  treat_missing_data = "notBreaching"
  tags               = var.tags
}

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  for_each = aws_lambda_function.this

  alarm_name          = "${each.value.function_name}-throttles"
  alarm_description   = "Telemetry Lab staging Lambda is throttled"
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  dimensions = {
    FunctionName = each.value.function_name
  }
  treat_missing_data = "notBreaching"
  tags               = var.tags
}

resource "aws_cloudwatch_metric_alarm" "lambda_duration" {
  for_each = aws_lambda_function.this

  alarm_name          = "${each.value.function_name}-duration"
  alarm_description   = "Telemetry Lab staging Lambda duration is elevated"
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10000
  comparison_operator = "GreaterThanThreshold"
  dimensions = {
    FunctionName = each.value.function_name
  }
  treat_missing_data = "notBreaching"
  tags               = var.tags
}
