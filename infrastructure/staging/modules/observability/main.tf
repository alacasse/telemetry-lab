resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = var.name_prefix
  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          region = var.aws_region
          title  = "Lambda Invocations and Errors"
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", var.function_names.ingestion],
            ["AWS/Lambda", "Errors", "FunctionName", var.function_names.ingestion],
            ["AWS/Lambda", "Invocations", "FunctionName", var.function_names.query],
            ["AWS/Lambda", "Errors", "FunctionName", var.function_names.query],
            ["AWS/Lambda", "Invocations", "FunctionName", var.function_names.worker],
            ["AWS/Lambda", "Errors", "FunctionName", var.function_names.worker],
            ["AWS/Lambda", "Invocations", "FunctionName", var.function_names.migration],
            ["AWS/Lambda", "Errors", "FunctionName", var.function_names.migration]
          ]
          stat   = "Sum"
          period = 300
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          region = var.aws_region
          title  = "API Gateway"
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiId", var.api_id],
            ["AWS/ApiGateway", "5xx", "ApiId", var.api_id],
            ["AWS/ApiGateway", "Latency", "ApiId", var.api_id]
          ]
          stat   = "Average"
          period = 300
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          region = var.aws_region
          title  = "Queue Health"
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.queue_name],
            ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", var.queue_name],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.dead_letter_queue_name]
          ]
          stat   = "Maximum"
          period = 300
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          region = var.aws_region
          title  = "RDS Health"
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.database_identifier],
            ["AWS/RDS", "FreeStorageSpace", "DBInstanceIdentifier", var.database_identifier],
            ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", var.database_identifier]
          ]
          stat   = "Average"
          period = 300
          view   = "timeSeries"
        }
      }
    ]
  })
}
