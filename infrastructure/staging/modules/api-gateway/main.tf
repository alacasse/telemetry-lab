locals {
  route_integrations = {
    "POST /telemetry"                        = "ingestion"
    "GET /health"                            = "query"
    "GET /buildings"                         = "query"
    "GET /buildings/{building_id}/state"     = "query"
    "GET /buildings/{building_id}/zones"     = "query"
    "GET /buildings/{building_id}/decisions" = "query"
    "GET /buildings/{building_id}/events"    = "query"
  }
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${var.name_prefix}-api"
  retention_in_days = 14
  tags              = var.tags
}

resource "aws_apigatewayv2_api" "this" {
  name          = "${var.name_prefix}-api"
  protocol_type = "HTTP"
  tags          = var.tags
}

resource "aws_apigatewayv2_integration" "service" {
  for_each = {
    ingestion = {
      invoke_arn = var.ingestion_lambda_invoke_arn
    }
    query = {
      invoke_arn = var.query_lambda_invoke_arn
    }
  }

  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = each.value.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 15000
}

resource "aws_apigatewayv2_route" "this" {
  for_each = local.route_integrations

  api_id    = aws_apigatewayv2_api.this.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.service[each.value].id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId         = "$context.requestId"
      ip                = "$context.identity.sourceIp"
      requestTime       = "$context.requestTime"
      httpMethod        = "$context.httpMethod"
      routeKey          = "$context.routeKey"
      status            = "$context.status"
      protocol          = "$context.protocol"
      responseLength    = "$context.responseLength"
      integrationStatus = "$context.integrationStatus"
      integrationError  = "$context.integrationErrorMessage"
      latency           = "$context.responseLatency"
    })
  }

  default_route_settings {
    throttling_burst_limit = 5
    throttling_rate_limit  = 2
  }

  tags = var.tags
}

resource "aws_lambda_permission" "ingestion_from_api_gateway" {
  statement_id  = "AllowApiGatewayInvokeIngestion"
  action        = "lambda:InvokeFunction"
  function_name = var.ingestion_lambda_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}

resource "aws_lambda_permission" "query_from_api_gateway" {
  statement_id  = "AllowApiGatewayInvokeQuery"
  action        = "lambda:InvokeFunction"
  function_name = var.query_lambda_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${var.name_prefix}-api-5xx"
  alarm_description   = "Telemetry Lab staging API Gateway reports 5xx responses"
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  dimensions = {
    ApiId = aws_apigatewayv2_api.this.id
  }
  treat_missing_data = "notBreaching"
  tags               = var.tags
}

resource "aws_cloudwatch_metric_alarm" "api_latency" {
  alarm_name          = "${var.name_prefix}-api-latency"
  alarm_description   = "Telemetry Lab staging API Gateway latency is elevated"
  namespace           = "AWS/ApiGateway"
  metric_name         = "Latency"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 1
  threshold           = 2000
  comparison_operator = "GreaterThanThreshold"
  dimensions = {
    ApiId = aws_apigatewayv2_api.this.id
  }
  treat_missing_data = "notBreaching"
  tags               = var.tags
}
