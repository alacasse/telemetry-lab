output "api_endpoint" {
  description = "Public API endpoint for staging."
  value       = module.api_gateway.api_endpoint
}

output "migration_function_name" {
  description = "Migration Lambda function name."
  value       = module.lambda_services.migration_function_name
}

output "lambda_function_names" {
  description = "Named Lambda functions used by staging."
  value       = module.lambda_services.function_names
}

output "queue_name" {
  description = "Telemetry queue name."
  value       = module.queue.queue_name
}

output "queue_url" {
  description = "Telemetry queue URL."
  value       = module.queue.queue_url
}

output "database_identifier" {
  description = "Database instance identifier."
  value       = module.database.instance_identifier
}

output "database_secret_arn" {
  description = "Secrets Manager ARN for the database runtime payload."
  value       = module.security.database_secret_arn
  sensitive   = true
}

output "staging_auth_secret_arn" {
  description = "Secrets Manager ARN for the staging access token."
  value       = module.security.staging_auth_secret_arn
  sensitive   = true
}

output "dashboard_name" {
  description = "CloudWatch dashboard name."
  value       = module.observability.dashboard_name
}
