locals {
  effective_queue_name   = var.queue_name != "" ? var.queue_name : "${var.name_prefix}-telemetry"
  dead_letter_queue_name = "${local.effective_queue_name}-dlq"
}

resource "aws_sqs_queue" "dead_letter" {
  name                      = local.dead_letter_queue_name
  message_retention_seconds = var.dead_letter_retention_seconds
  kms_master_key_id         = var.kms_key_id
  sqs_managed_sse_enabled   = var.kms_key_id == null

  tags = merge(var.tags, {
    Name = local.dead_letter_queue_name
  })
}

resource "aws_sqs_queue" "telemetry" {
  name                       = local.effective_queue_name
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = var.message_retention_seconds
  kms_master_key_id          = var.kms_key_id
  sqs_managed_sse_enabled    = var.kms_key_id == null
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = merge(var.tags, {
    Name = local.effective_queue_name
  })
}

resource "aws_cloudwatch_metric_alarm" "queue_age_high" {
  alarm_name          = "${var.name_prefix}-queue-age-high"
  alarm_description   = "Telemetry Lab staging telemetry queue age is high"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 300
  comparison_operator = "GreaterThanThreshold"
  dimensions = {
    QueueName = aws_sqs_queue.telemetry.name
  }
  treat_missing_data = "notBreaching"
  tags               = var.tags
}

resource "aws_cloudwatch_metric_alarm" "dead_letter_visible" {
  alarm_name          = "${var.name_prefix}-dlq-visible"
  alarm_description   = "Telemetry Lab staging DLQ contains messages"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  dimensions = {
    QueueName = aws_sqs_queue.dead_letter.name
  }
  treat_missing_data = "notBreaching"
  tags               = var.tags
}
