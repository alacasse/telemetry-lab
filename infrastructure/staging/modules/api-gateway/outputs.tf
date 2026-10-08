output "api_id" {
  value = aws_apigatewayv2_api.this.id
}

output "api_name" {
  value = aws_apigatewayv2_api.this.name
}

output "api_endpoint" {
  value = aws_apigatewayv2_stage.default.invoke_url
}

output "stage_name" {
  value = aws_apigatewayv2_stage.default.name
}
