output "function_names" {
  value = {
    for service, function in aws_lambda_function.this : service => function.function_name
  }
}

output "function_arns" {
  value = {
    for service, function in aws_lambda_function.this : service => function.arn
  }
}

output "function_invoke_arns" {
  value = {
    for service, function in aws_lambda_function.this : service => function.invoke_arn
  }
}

output "ingestion_function_name" {
  value = aws_lambda_function.this["ingestion"].function_name
}

output "worker_function_name" {
  value = aws_lambda_function.this["worker"].function_name
}

output "query_function_name" {
  value = aws_lambda_function.this["query"].function_name
}

output "migration_function_name" {
  value = aws_lambda_function.this["migration"].function_name
}
